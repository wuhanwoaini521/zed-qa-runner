# Acceptance report

Date: 2026-10-16 · Environment: WSL2 Ubuntu 22.04, Python 3.13.16, pytest 9.1.1,
pytest-xdist 3.8.0, pytest-rerunfailures 16.7, pytest-playwright 0.10.0,
playwright 1.64.0 (real Chromium), Allure CLI 2.44.1 (Windows binary via WSL),
Zed 0.x at `/mnt/d/soft/Zed/bin/zed`.

All results below come from a run **after** the last code change.

## Automated test results

```
71 passed, 0 failed, 0 errors, 0 skipped in 120.23s
```

`tests/unit` (38), `tests/integration` (28, real pytest subprocesses incl. real
Chromium), `tests/e2e` (3).

| Gate | Command | Result |
| --- | --- | --- |
| Tests | `python -m pytest -q` | 71 passed |
| Lint | `ruff check src tests` | All checks passed |
| Format | `ruff format --check src tests` | 45 files already formatted |
| Types | `mypy src` | no issues in 33 source files |

## Real execution evidence

Full demo project (16 cases), resource strategy, 4 workers, 1 rerun:

```
discover: 16 cases, 0 errors
plan: 2 groups, effective parallelism 2
state: failed  counts: passed=13 failed=2 flaky=1 total=16
group overlap violations: []
  resource:grp001 ['account:A','region:JP'] -> all 9 cases on gw0
  resource:grp002 ['account:B','account:C','region:US'] -> all 7 cases on gw1
flaky: tests/test_search.py::TestSearch::test_flaky -> flaky (2 attempts, first failure kept)
pw failure screenshots: 2
allure report: examples/demo_pytest_project/.qa-runs/<run-id>/allure-report/index.html exists
report_error: None
```

Note `grp002` merges account B and account C through the shared `region:US`
resource — a **transitive** conflict — and still lands on one worker. Different
accounts (A vs B) ran concurrently; that concurrency is asserted in
`test_same_account_never_overlaps` via measured start/end timestamps.

## Acceptance scenario matrix (task §19)

| # | Scenario | Test | Status |
| --- | --- | --- | --- |
| 1 | Single case correct | `test_only_selected_cases_run`, `test_run_single_passing_case` | PASS |
| 2 | Cross-file 5 cases, only those | `test_full_workflow_...`, `test_only_selected_cases_run` | PASS |
| 3 | Same class not concurrent | `test_class_strategy_pins_class_to_one_worker` | PASS |
| 4 | Different class + different account parallel | `test_same_account_never_overlaps` (asserts overlap > 0) | PASS |
| 5 | Different class + same account not parallel | `test_same_account_never_overlaps` (asserts no overlap) | PASS |
| 6 | Transitive resource conflict | `test_resource_transitive_conflict`, demo `grp002` | PASS |
| 7 | 10 cases / 3 workers complete | `test_resource_strategy_mutual_exclusion` (16 cases, 4 workers; every selected case terminal, none lost) | PASS |
| 8 | Worker crash → correct state | `test_worker_crash_does_not_hang` | PASS |
| 9 | Report generated after failure | `test_report_generated_despite_failures` | PASS |
| 10 | Rerun Failed only selected | `test_full_workflow_...`, `test_rerun_failed_only` | PASS |
| 11 | Parameterized cases selectable separately | `test_demo_discovery_matches_pytest`, `test_nodeid_with_spaces_and_brackets` | PASS |
| 12 | Cancel releases resources | `test_cancellation_releases_resources` | PASS |
| 13 | Two runs don't pollute | `test_two_runs_do_not_pollute` | PASS |
| 14 | Debug run has no xdist | `test_debug_configs_disable_xdist_and_reruns` | PASS |
| 15 | Zed Task and CLI share config | `test_zed_task_and_debug_are_consistent` (single `ProjectService`) | PASS |
| 16 | Existing Zed config not overwritten | `test_init_merges_and_uninstalls`, `test_merge_does_not_overwrite_existing` | PASS |
| 17 | Flaky initial failure evidence kept | `test_flaky_keeps_failure_evidence` | PASS |
| 18 | Windows/WSL paths & argv | `test_nodeid_with_spaces_and_brackets`, `test_allure_path_conversion_returns_string`, `test_build_args_is_argv_list...` | PASS (WSL) |

## Zed integration

- `zedqa init` writes/merges `.zed/tasks.json` (7 tasks) and `.zed/debug.json`
  (4 Debugpy profiles). Verified by reading the generated files in the demo
  project and by CLI/integration tests.
- Debug profiles always include `-n0` and `-p no:rerunfailures`.
- Zed extension API limits confirmed against the official docs: extensions cannot
  create side panels, hence the Textual TUI + Tasks + Debugpy + Allure approach.

## NOT VERIFIED

These are environment limits, not hidden failures:

- **Interactive Zed GUI**: launching the task inside the real Zed terminal and
  starting a live Debugpy session were not automated (no Zed GUI harness). The
  TUI is verified headless with Textual's test pilot; the task/debug files are
  verified by content and against the documented schema.
- **Native Windows and macOS execution**: only WSL2/Ubuntu was available. argv
  construction, path conversion and WSL/Windows Allure path handling are tested,
  but a native Windows/macOS run is not.
- **Allure 3 CLI**: only Allure 2.44.1 was installed. Version detection and
  fallback for `--clean` are implemented but not exercised against Allure 3.
- **Cross-machine resource locking**: only same-machine file locks are
  implemented and tested. No Redis/network backend is claimed.

## Known limitations

- Trace/video files from `pytest-playwright` are collected into the run dir and
  best-effort attached to their test; the authoritative failure screenshot is
  captured by zedqa's own plugin.
- `resource` mode treats the class as an implicit resource by default
  (`include_class_in_resource_mode = true`); disable it to allow same-class
  parallelism (not recommended).
- Run history is a directory listing; there is no trend database.
