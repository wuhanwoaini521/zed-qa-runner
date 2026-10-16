# Usage

## Test Explorer (TUI)

```bash
zedqa ui          # or the Zed task "zedqa: Test Explorer (TUI)"
```

Layout: Test Explorer left, run configuration right, live monitor bottom.

| Key | Action |
| --- | --- |
| `space` | Toggle the highlighted case |
| `a` / `n` / `i` | Select all visible / none / invert |
| `f` | Select the failed cases of the latest run |
| `r` | Run the current selection |
| `p` | Print the schedule preview |
| `c` | Cycle strategy (case → class → file → resource) |
| `+` / `-` | More / fewer workers |
| `t` | Cycle reruns |
| `h` | Toggle headless |
| `s` | Stop the running batch |
| `o` | Open the latest Allure report |
| `q` | Quit |

The search box filters by nodeid, file, class and marker. Parameterized cases are
distinct rows and can be selected individually.

## Selection on the CLI

```bash
zedqa run --all
zedqa run --file tests/test_login.py --file tests/test_cart.py
zedqa run --class TestLogin --class TestCart
zedqa run --marker smoke --marker slow
zedqa run --keyword login --workers 4
zedqa run --nodeid 'tests/test_login.py::TestLogin::test_login_param[bob]'
```

Unknown `--nodeid` values abort the run with an error — nothing is silently
substituted.

## Flaky tests

`--reruns N` uses pytest-rerunfailures. A test that fails and then passes is
reported as **flaky**, its original failure text and screenshot are kept, and it
is not counted as a pass. If every attempt fails it stays failed.

## Failure Center

```bash
zedqa failures --latest            # human summary
zedqa failures <run-id> --json     # machine readable
```

Each failure shows status, error type, phase, worker, attempts, screenshot/trace/
video paths and the traceback. Rerun only the failures with the exact nodeids:

```bash
zedqa rerun --latest --open
zedqa rerun --latest --nodeid 'tests/test_search.py::TestSearch::test_flaky'
```

## Debugging in Zed

```bash
zedqa debug --nodeid 'tests/test_login.py::TestLogin::test_failure'
zedqa debug --latest            # configs for all failed cases of the latest run
```

Then in Zed open the **Debugger** panel and pick the `zedqa: Debug …`
configuration. Debug profiles always pass `-n0` and `-p no:rerunfailures`, so the
debugger attaches to the single test process. Use the
`zedqa: Debug Playwright (headed, PWDEBUG)` profile for browser inspection.

## Saved suites

```bash
zedqa suites create smoke --marker smoke
zedqa suites create login-reg --file tests/test_login.py
zedqa suites create promo --nodeid 'tests/test_cart.py::TestCart::test_add'
zedqa suites show promo
zedqa suites run promo --workers 4
```

A suite can store exact nodeids or a dynamic filter. If a saved nodeid no longer
exists, `suites show`/`suites run` report it as **missing** instead of running a
different test.

## Environment

```bash
zedqa doctor
```

Checks OS/WSL, interpreter, pytest and plugins, Playwright browsers, Allure CLI,
and whether `.zed/` integration exists. Required checks fail with exit code 1;
optional ones warn.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `ZEDQA_PYTHON` | Force the project interpreter. |
| `ZEDQA_ALLURE` | Force the Allure CLI path. |
| `PLAYWRIGHT_BROWSERS_PATH` | Standard Playwright override (honored). |

Runtime variables (`ZEDQA_RUN_ID`, `ZEDQA_EVENT_FILE`, …) are set by the runner
for the child pytest process only and are not meant to be set by hand.
