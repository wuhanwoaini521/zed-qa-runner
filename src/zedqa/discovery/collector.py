"""Test discovery by invoking the target project's real pytest collection."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from zedqa.core.interpreter import pytest_command, resolve_interpreter
from zedqa.core.models import CollectionError, DiscoveryResult, TestCase


def _plugin_pythonpath() -> str:
    """Directory holding the `zedqa` package, so the target pytest can import it."""
    return str(Path(__file__).resolve().parents[2])


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _plugin_pythonpath() + (os.pathsep + existing if existing else "")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if extra:
        env.update(extra)
    return env


def build_collect_command(
    interpreter: str,
    markers: list[str] | None = None,
    keyword: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    cmd = [
        *pytest_command(interpreter),
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "zedqa.discovery.collect_plugin",
    ]
    for marker in markers or []:
        cmd += ["-m", marker]
    if keyword:
        cmd += ["-k", keyword]
    cmd += list(extra_args or [])
    return cmd


def discover(
    root: Path | str,
    interpreter: str | None = None,
    markers: list[str] | None = None,
    keyword: str | None = None,
    extra_args: list[str] | None = None,
) -> DiscoveryResult:
    root = Path(root).resolve()
    py = resolve_interpreter(root, interpreter)
    cmd = build_collect_command(py, markers, keyword, extra_args)

    with tempfile.TemporaryDirectory(prefix="zedqa-collect-") as tmp:
        out = Path(tmp) / "collect.json"
        env = build_env({"ZEDQA_COLLECT_OUT": str(out)})
        started = time.perf_counter()
        import subprocess

        proc = subprocess.run(cmd, cwd=str(root), env=env, capture_output=True, text=True)
        duration = time.perf_counter() - started
        payload: dict = {}
        if out.exists():
            try:
                payload = json.loads(out.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}

    cases = [TestCase.model_validate(c) for c in payload.get("cases", [])]
    errors = [CollectionError.model_validate(e) for e in payload.get("errors", [])]
    if not payload and proc.returncode not in (0, 5):
        # Collection crashed before the plugin could write anything.
        errors.append(
            CollectionError(message=(proc.stderr or proc.stdout or "collection failed").strip())
        )
    return DiscoveryResult(
        root=str(root),
        interpreter=py,
        cases=cases,
        errors=errors,
        duration=duration,
        command=cmd,
    )
