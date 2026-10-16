"""Resolve the interpreter that owns the target project's pytest environment.

We never run pytest with the zedqa interpreter by accident: the target project may
have its own virtualenv with a different pytest/playwright version.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _candidate_interpreters(root: Path) -> list[Path]:
    cands: list[Path] = []
    if env := os.environ.get("ZEDQA_PYTHON"):
        cands.append(Path(env))
    if env := os.environ.get("VIRTUAL_ENV"):
        cands.append(Path(env) / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    for name in (".venv", "venv", "env"):
        for sub in ("bin/python", "Scripts/python.exe"):
            cands.append(root / name / sub)
    if v := os.environ.get("CONDA_PREFIX"):
        cands.append(Path(v) / ("python.exe" if os.name == "nt" else "bin/python"))
    return cands


def resolve_interpreter(root: Path, explicit: str | None = None) -> str:
    """Return a python executable path for the target project.

    Priority: explicit arg > ZEDQA_PYTHON > active venv > project venvs > current python.
    """
    if explicit:
        return explicit
    for cand in _candidate_interpreters(root):
        if cand.exists():
            return str(cand)
    return sys.executable


def pytest_command(interpreter: str) -> list[str]:
    """argv prefix to invoke pytest without a shell."""
    return [interpreter, "-m", "pytest"]


def check_import(interpreter: str, module: str) -> tuple[bool, str]:
    """Return (importable, detail). Used by doctor."""
    try:
        proc = subprocess.run(
            [interpreter, "-c", f"import {module}; print(getattr({module}, '__version__', 'ok'))"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, proc.stdout.strip()
    return False, (proc.stderr.strip().splitlines() or ["import failed"])[-1]


def find_allure() -> str | None:
    """Locate the Allure CLI. On WSL the Windows shim may be on PATH."""
    if env := os.environ.get("ZEDQA_ALLURE"):
        return env
    exe = shutil.which("allure")
    if exe:
        return exe
    for win in ("/mnt/c", "/mnt/d"):
        for rel in ("Scoop/shims/allure.cmd", "Scoop/shims/allure"):
            p = Path(win) / rel
            if p.exists():
                return str(p)
    return None
