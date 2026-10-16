# Zed QA Runner (`zedqa`)

A Zed-friendly test workbench for Python + pytest + Playwright projects:
discover real pytest cases, multi-select them, schedule them safely around
Class/account conflicts, run them with live monitoring, locate failures, rerun and
debug them, and open the Allure report — without modifying your test code.

Execution does **not** depend on Zed. Everything works from the CLI and a Textual
TUI; Zed is just a convenient entry point through Tasks and the built-in Debugpy
debugger.

## Why a TUI and not a Zed side panel?

Zed's extension API can contribute **languages, themes, debuggers, snippets and
MCP servers**, but it cannot create arbitrary side panels or tree views
([Zed extension docs](https://zed.dev/docs/extensions/developing-extensions)).
So `zedqa` ships:

- a **Textual TUI** (Test Explorer + live monitor) launched from a Zed Task,
- **Zed Tasks** (`.zed/tasks.json`) for one-key actions,
- **Zed Debugpy** (`.zed/debug.json`) for breakpoint debugging,
- the **native Allure HTML report** for browsing results.

No Zed fork, no separate frontend/backend platform.

## Install

Requirements: Python 3.12+ (3.13 supported), and for the target project:
`pytest`, and optionally `pytest-xdist`, `pytest-rerunfailures`, `allure-pytest`,
`pytest-playwright` + `playwright`, and the **Allure CLI**.

```bash
# from this repo
uv venv && uv pip install -e ".[dev]"      # dev: includes playwright + lint tools
# or, for a normal install
pip install .
```

Then, inside the project you want to test:

```bash
zedqa init          # writes zedqa.toml, merges .zed/tasks.json and .zed/debug.json
zedqa doctor        # verify the toolchain
zedqa ui            # open the Test Explorer TUI
```

`zedqa init` never overwrites existing Zed files. It merges by label, backs the
previous file up to `*.zedqa-backup`, and can be undone:

```bash
zedqa init --uninstall   # remove only zedqa entries
zedqa init --restore     # restore the backed-up .zed files
```

Set `ZEDQA_PYTHON=/path/to/project/venv/bin/python` if auto-detection picks the
wrong interpreter. `ZEDQA_ALLURE` overrides Allure CLI discovery.

## Quick start (CLI)

```bash
# 1. discover (real pytest collection, never executes tests)
zedqa discover

# 2. preview the schedule without running
zedqa plan --file tests/test_login.py --workers 4 --strategy resource

# 3. run exactly what you selected
zedqa run --nodeid 'tests/test_login.py::TestLogin::test_success' \
          --nodeid 'tests/test_cart.py::TestCart::test_add' \
          --workers 4 --strategy resource --reruns 1

# 4. inspect failures, rerun just the failed cases, open the report
zedqa failures --latest
zedqa rerun --latest --open
zedqa report --latest --open
```

## Commands

| Command | Purpose |
| --- | --- |
| `zedqa init` | Install/merge/uninstall Zed Tasks + Debug configs. |
| `zedqa doctor` | Check OS/WSL, interpreter, pytest plugins, Playwright, Allure, Zed config. |
| `zedqa discover` | List cases from real pytest collection (`--json` for machines). |
| `zedqa plan` | Schedule preview (groups, conflicts, effective parallelism). |
| `zedqa run` | Run a selection with a unified Run ID. |
| `zedqa ui` | Interactive Test Explorer + live run monitor. |
| `zedqa runs` | Run history. |
| `zedqa failures` | Failed cases with error, location and evidence. |
| `zedqa rerun` | Rerun failed cases by exact nodeid with the original config. |
| `zedqa report` | Generate/open the Allure report for a run. |
| `zedqa debug` | Write a Debugpy config for a test. |
| `zedqa suites` | Saved suites: `list/create/show/delete/run`. |
| `zedqa status` | One-line project/toolchain summary. |

Every command has `--help`. The CLI and TUI call the same `ProjectService`, so
there is only one execution path.

## Scheduling strategies

Set with `--strategy` (or `c` in the TUI). Worker count with `--workers`/`-n`.

| Strategy | xdist | Guarantee |
| --- | --- | --- |
| `case` | `--dist=load` | Selected cases may run in parallel. |
| `class` (default) | `--dist=loadscope` | Same module+class runs on one worker, in order. |
| `file` | `--dist=loadfile` | Same file runs on one worker. |
| `resource` | `--dist=loadgroup` + collection hook | Any cases sharing a resource (transitively) share a worker. |

`resource` mode builds a conflict graph with union-find over every resource id
(accounts, regions, shared infra) and injects `@pytest.mark.xdist_group` before
xdist schedules. On top of that, a per-resource **file lock** prevents two
independent `zedqa` runs on the same machine from touching the same resource.

## `zedqa.toml`

```toml
version = 1

[scheduling]
default_workers = 4
default_strategy = "resource"
include_class_in_resource_mode = true   # same class = implicit conflict

[resources]
lock_dir = ".qa-runs/.locks"
lock_timeout = 30.0      # seconds to wait for a cross-process resource lock
stale_after = 600.0      # reclaim locks from crashed processes

[[resources.rules]]
match = "tests/test_login.py::TestLogin"   # glob on nodeid / class key / file
resources = ["account:A", "region:JP"]

[browser]
browser = "chromium"
headless = true

[reporting]
runs_dir = ".qa-runs"
open_report = false
```

**Never put passwords, tokens or cookies here.** Rule values are aliases only;
real credentials stay in the project's own environment.

## Run artifacts

Every run gets its own directory, so parallel runs never contaminate each other:

```
.qa-runs/<run-id>/
├── run.json              # manifest: config, plan, results, counts, state
├── events.jsonl          # structured run events (source of truth for the UI)
├── group-map.json        # nodeid -> xdist group
├── resource-map.json     # nodeid -> resources
├── junit.xml
├── logs/pytest.log
├── allure-results/       # all workers merged
├── allure-report/        # generated HTML
├── screenshots/          # captured on failure
└── playwright-artifacts/ # traces/videos (pytest-playwright)
```

## Testing this project

```bash
uv pip install -e ".[dev]"
.venv/bin/python -m pytest          # unit + integration + e2e
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

The suite runs **real** pytest subprocesses against
`examples/demo_pytest_project` (including real Chromium), including a synthetic
project that proves same-account tests never overlap in time.

See `docs/` for architecture, usage and the Zed integration details, and
`docs/acceptance.md` for the verified acceptance results and known limitations.
