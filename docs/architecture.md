# Architecture

## Goals

- Execution core is UI-independent and runs without Zed.
- Do not reimplement pytest, xdist, Playwright, Allure, or Debugpy.
- Do not require test authors to change their tests or fixtures.
- One business layer (`ProjectService`) shared by CLI and TUI.

## Layers

```
                       ┌─────────────┐   ┌─────────────┐
   Zed Task ─────────► │  cli/main   │   │  tui/app    │ ◄──── Zed Task
                       └──────┬──────┘   └──────┬──────┘
                              └───────┬─────────┘
                                      ▼
                            core/service.ProjectService
            ┌───────────────┬─────────┼───────────┬───────────────┐
            ▼               ▼         ▼           ▼               ▼
     discovery/       scheduler/   runner/     reporting/      resources/
     collect_plugin   planner      executor    allure,store    locks
                                   pytest_plugin
```

| Module | Responsibility |
| --- | --- |
| `core/models.py` | Pydantic models shared across boundaries. |
| `core/config.py` | `zedqa.toml` load/save. |
| `core/interpreter.py` | Find the project interpreter, pytest argv, Allure CLI. |
| `core/doctor.py` | Environment checks. |
| `core/selection.py` | nodeid/file/class/marker/keyword selection. |
| `core/service.py` | The one orchestration layer (discover → plan → run → report). |
| `discovery/collect_plugin.py` | Runs inside the **target** pytest, emits collected items as JSON. |
| `discovery/collector.py` | Invokes `pytest --collect-only` and parses the JSON. |
| `scheduler/planner.py` | Pure grouping logic (case/class/file/resource + union-find). |
| `runner/pytest_plugin.py` | Runs inside the target pytest during execution: events, group markers, resource locks, screenshots. |
| `runner/executor.py` | Builds argv, launches pytest, tails the event stream. |
| `runner/aggregator.py` | Folds events into `CaseResult` state (incl. flaky + first failure). |
| `resources/locks.py` | Cross-process file locks with stale/PID handling. |
| `reporting/` | Allure generation (WSL-aware) and run manifest persistence. |
| `debugging/`, `integrations/` | Zed Debugpy configs and safe Tasks/Debug merge. |
| `tui/` | Textual UI. No execution logic. |

## Why collection is a pytest plugin

Discovery must reflect pytest's real collection (parametrization, markers,
classes, collection errors) and must never execute a test. `collect_plugin`
hooks `pytest_collection_modifyitems` with `trylast=True` (so marker/keyword
deselection is already applied) and writes a structured JSON file; a
`pytest_sessionfinish` fallback guarantees errors are captured even when
collection aborts.

The plugin is injected into the target project's pytest by adding the `zedqa`
package directory to `PYTHONPATH` for the subprocess — the target project does
not need zedqa installed in its own venv.

## Why resource mode uses a collection hook + file locks

xdist's `--dist=loadgroup` groups tests by the `@pytest.mark.xdist_group` marker.
`zedqa` computes the groups itself and injects the markers in a
`tryfirst=True` collection hook (xdist reads the marker later to append its
`@group` nodeid suffix). This gives transitive grouping: if A shares a resource
with B and B with C, all three land on one worker.

Because grouping only protects **within one run**, a second file-lock layer
protects across runs on the same machine. Locks are acquired in sorted order
(deadlock-free), roll back on partial acquisition, and reclaim stale locks from
dead PIDs.

## Real-time events

`runner/pytest_plugin.py` writes one JSON line per event to
`events.jsonl` with `O_APPEND` (atomic across workers). Events carry run id,
nodeid, worker, attempt, phase and outcome. The controller suppresses duplicate
events in distributed mode; nodeids have the xdist `@group` suffix stripped so
they match the selection. The same `RunAggregator` powers the live TUI and the
persisted manifest, so the UI and the saved result cannot disagree.

## Deliberate non-goals (V1)

- No Redis/network lock backend is shipped; cross-machine mutual exclusion is
  **not claimed**.
- No AI analysis, historical trends or Jenkins integration.
- Trace/video files produced by `pytest-playwright` are kept in the run dir and
  best-effort attached to their test by name; the authoritative screenshot
  evidence is captured by our own plugin.
