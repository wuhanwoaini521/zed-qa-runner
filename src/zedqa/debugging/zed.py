"""Generate Zed Debugpy (DAP) launch configurations for pytest.

Debug runs must disable xdist and reruns so the debugger attaches to the right
process and every breakpoint is hit exactly once.
"""

from __future__ import annotations

from pathlib import Path

# xdist/rr are disabled here; debugging a distributed or auto-retrying run
# attaches the debugger to the wrong process.
DEBUG_ARGS = ["-n0", "-p", "no:rerunfailures", "-s", "--no-header", "-vv"]


def _python(interpreter: str | None) -> str:
    return interpreter or "python"


def debug_configs(interpreter: str | None = None, venv: str | None = None) -> list[dict]:
    py = venv or _python(interpreter)
    base = {
        "adapter": "Debugpy",
        "request": "launch",
        "module": "pytest",
        "cwd": "$ZED_WORKTREE_ROOT",
        "justMyCode": False,
        "python": py,
    }
    return [
        {
            **base,
            "label": "zedqa: Debug current test file",
            "args": ["$ZED_RELATIVE_FILE", *DEBUG_ARGS],
        },
        {
            **base,
            "label": "zedqa: Debug current test symbol",
            "args": ["$ZED_RELATIVE_FILE::$ZED_SYMBOL", *DEBUG_ARGS],
        },
        {
            **base,
            "label": "zedqa: Debug last failed tests (--lf)",
            "args": ["--lf", *DEBUG_ARGS],
        },
        {
            **base,
            "label": "zedqa: Debug Playwright (headed, PWDEBUG)",
            "args": ["--lf", "--headed", *DEBUG_ARGS],
            "env": {"PWDEBUG": "1"},
        },
    ]


def config_for_nodeid(nodeid: str, venv: str | None = None) -> dict:
    return {
        "label": f"zedqa: Debug {nodeid.split('::')[-1]}",
        "adapter": "Debugpy",
        "request": "launch",
        "module": "pytest",
        "args": [nodeid, *DEBUG_ARGS],
        "cwd": "$ZED_WORKTREE_ROOT",
        "justMyCode": False,
        "python": venv or "python",
    }


def find_venv_python(root: Path) -> str | None:
    for rel in (
        ".venv/bin/python",
        ".venv/Scripts/python.exe",
        "venv/bin/python",
        "venv/Scripts/python.exe",
    ):
        p = root / rel
        if p.exists():
            return f"$ZED_WORKTREE_ROOT/{rel}"
    return None
