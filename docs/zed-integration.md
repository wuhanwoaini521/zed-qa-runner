# Zed integration

Verified against the official Zed docs:

- [Developing Extensions](https://zed.dev/docs/extensions/developing-extensions):
  extensions may provide languages, themes, debuggers, snippets and MCP servers.
  They **cannot** create side panels, tree views or custom toolbars.
- [Tasks](https://zed.dev/docs/tasks): `.zed/tasks.json` arrays with `label`,
  `command`, `args`, `cwd`, `env`, `reveal`, `use_new_terminal` and `$ZED_*`
  variables.
- [Debugger](https://zed.dev/docs/debugger): `.zed/debug.json` array; Python uses
  the built-in **Debugpy** adapter.
- [MCP](https://zed.dev/docs/ai/mcp): optional, not required for V1.

## What zedqa generates

`zedqa init` merges these entries into your existing files (existing entries win,
previous files are backed up once):

`.zed/tasks.json`

| Task | Command |
| --- | --- |
| zedqa: Test Explorer (TUI) | `zedqa ui` |
| zedqa: Run current test file | `zedqa run --file $ZED_RELATIVE_FILE` |
| zedqa: Run current test symbol | `zedqa run --nodeid $ZED_RELATIVE_FILE::$ZED_SYMBOL` |
| zedqa: Rerun failed (latest run) | `zedqa rerun --latest` |
| zedqa: Open Allure report (latest run) | `zedqa report --latest --open` |
| zedqa: Discover tests | `zedqa discover` |
| zedqa: Doctor | `zedqa doctor` |

`.zed/debug.json` (adapter `Debugpy`, all include `-n0 -p no:rerunfailures`)

- `zedqa: Debug current test file`
- `zedqa: Debug current test symbol`
- `zedqa: Debug last failed tests (--lf)`
- `zedqa: Debug Playwright (headed, PWDEBUG)`

`zedqa debug --nodeid …` appends a concrete profile for one test, replacing any
older profile with the same label.

## Using it

1. Open the project in Zed.
2. Command palette → **task: spawn** → `zedqa: Test Explorer (TUI)`.
3. To debug: open the **Debugger** panel (or run `zedqa debug --latest` first),
   choose a `zedqa: Debug …` configuration, set breakpoints, start.

## Safety

- `zedqa init` is additive and idempotent; it fails loudly if an existing file is
  not valid JSON rather than clobbering it.
- `zedqa init --uninstall` removes only entries whose label starts with `zedqa:`.
- `zedqa init --restore` restores the `*.zedqa-backup` copies.

## If you prefer the native pytest debugging

Zed can already detect pytest entry points and start a debug session without a
config (F4 / `debugger: start`). The generated profiles exist so the exact nodeid
and the xdist/rerun-disabling flags are consistent with `zedqa`.

## MCP (optional, future)

An MCP server exposing `discover` / `run` / `failures` is a natural extension,
but it is not required for the V1 workflow and is not implemented here.
