"""Pytest plugin used for `--collect-only` discovery.

Loaded into the *target* project's pytest via `-p zedqa.discovery.collect_plugin`.
It only reads pytest's collection result; it never runs a test.
Output path comes from the ZEDQA_COLLECT_OUT environment variable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _rel(path: str | Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    try:
        return repr(value)
    except Exception:  # noqa: BLE001 - discovery must never crash on odd params
        return f"<{type(value).__name__}>"


_ERRORS: list[dict] = []
_CASES: list[dict] = []
_ROOT = ""
_WRITTEN = False


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):  # noqa: ANN001, ARG001
    global _ROOT, _CASES
    root = Path(str(config.rootpath)).resolve()
    _ROOT = str(root)
    cases = []
    for item in items:
        callspec = getattr(item, "callspec", None)
        params: dict[str, str] = {}
        param_id = None
        if callspec is not None:
            params = {str(k): _stringify(v) for k, v in callspec.params.items()}
            param_id = callspec.id
        markers = sorted({m.name for m in item.iter_markers()})
        cls = getattr(item, "cls", None)
        function = getattr(item, "function", None)
        cases.append(
            {
                "nodeid": item.nodeid,
                "name": item.name,
                "file": _rel(getattr(item, "path", item.fspath), root),
                "line": getattr(item, "location", (None, None, None))[1],
                "cls": getattr(cls, "__name__", None),
                "function": getattr(function, "__name__", None),
                "params": params,
                "param_id": param_id,
                "markers": markers,
            }
        )
    payload = {
        "root": str(root),
        "cases": cases,
        "errors": list(_ERRORS),
    }
    _CASES = cases
    _write(payload)


def _write(payload: dict) -> None:
    global _WRITTEN
    out = os.environ.get("ZEDQA_COLLECT_OUT")
    if out:
        Path(out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _WRITTEN = True


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ARG002
    # Guarantee an output file even if collection aborted before modifyitems ran.
    if not _WRITTEN:
        _write({"root": _ROOT, "cases": _CASES, "errors": list(_ERRORS)})


def pytest_collectreport(report):  # noqa: ANN001
    if not report.failed:
        return
    nodeid = getattr(report, "nodeid", None) or ""
    _ERRORS.append({"file": nodeid.split("::")[0] or None, "message": str(report.longrepr)})
