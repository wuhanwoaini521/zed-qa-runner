"""Pytest plugin used for real execution.

Responsibilities, all opt-in through environment variables so it is inert when a
project runs plain `pytest`:

  ZEDQA_EVENT_FILE       JSONL stream of structured run events (real-time UI)
  ZEDQA_GROUP_MAP        JSON file {nodeid: xdist_group} applied via collection hook
  ZEDQA_RESOURCE_MAP     JSON file {nodeid: [resource ids]} enforced with file locks
  ZEDQA_RESOURCE_LOCK_DIR directory for cross-process lock files
  ZEDQA_LOCK_TIMEOUT     seconds to wait for a resource lock
  ZEDQA_EVIDENCE_DIR     base dir for screenshots on failure
  ZEDQA_RUN_ID           run id attached to every event
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover
    pytest = None  # type: ignore

_RUN_ID = os.environ.get("ZEDQA_RUN_ID", "unknown")
_EVENT_FILE = os.environ.get("ZEDQA_EVENT_FILE")
_EVIDENCE_DIR = os.environ.get("ZEDQA_EVIDENCE_DIR")
_ATTEMPTS: dict[str, int] = {}
_LOCKS = None  # ResourceLockManager | None
_HELD: dict[str, object] = {}
_DISTRIBUTED = False


def _emit(payload: dict) -> None:
    if not _EVENT_FILE:
        return
    payload.setdefault("run_id", _RUN_ID)
    payload.setdefault("timestamp", time.time())
    try:
        fd = os.open(_EVENT_FILE, os.O_CREAT | os.O_APPEND | os.O_WRONLY)
        try:
            os.write(fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        pass


def _worker() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "master")


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[:180]


def _clean_nodeid(nodeid: str) -> str:
    """Drop the `@group` suffix xdist appends for --dist=loadgroup."""
    at = nodeid.rfind("@")
    if at > nodeid.rfind("]") and at != -1:
        return nodeid[:at]
    return nodeid


def _load_map(env_name: str) -> dict:
    path = os.environ.get(env_name)
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def pytest_configure(config):  # noqa: ANN001
    global _DISTRIBUTED
    if pytest is not None:
        config.addinivalue_line("markers", "zedqa: reserved for zedqa runner")
    # With xdist, the controller also receives every worker's logreport hook,
    # so emitting there would duplicate every event. Emit from workers only.
    try:
        nprocs = config.getoption("numprocesses", 0) or 0
    except (ValueError, KeyError):  # pragma: no cover
        nprocs = 0
    _DISTRIBUTED = bool(os.environ.get("PYTEST_XDIST_WORKER")) or int(nprocs) > 0


def _tryfirst(func):
    return pytest.hookimpl(tryfirst=True)(func) if pytest is not None else func


@_tryfirst
def pytest_collection_modifyitems(config, items):  # noqa: ANN001, ARG001
    """Inject xdist_group markers computed by the scheduler (resource strategy).

    Must run before xdist's own collection hook, which reads the marker to append
    the `@group` suffix used by --dist=loadgroup.
    """
    group_map = _load_map("ZEDQA_GROUP_MAP")
    if not group_map or pytest is None:
        return
    applied = 0
    for item in items:
        group = group_map.get(item.nodeid)
        if group:
            item.add_marker(pytest.mark.xdist_group(name=group))
            applied += 1
    if applied:
        _emit({"type": "log", "message": f"applied {applied} xdist_group markers"})


def _get_lock_manager():
    global _LOCKS
    if _LOCKS is None:
        from zedqa.resources.locks import ResourceLockManager

        lock_dir = os.environ.get("ZEDQA_RESOURCE_LOCK_DIR")
        if not lock_dir:
            return None
        _LOCKS = ResourceLockManager(Path(lock_dir))
    return _LOCKS


def pytest_runtest_setup(item):  # noqa: ANN001
    nodeid = _clean_nodeid(item.nodeid)
    _ATTEMPTS[nodeid] = _ATTEMPTS.get(nodeid, 0) + 1
    _emit({"type": "start", "nodeid": nodeid, "worker": _worker(), "attempt": _ATTEMPTS[nodeid]})

    resource_map = _load_map("ZEDQA_RESOURCE_MAP")
    resources = resource_map.get(nodeid)
    if not resources:
        return
    manager = _get_lock_manager()
    if manager is None:
        return
    timeout = float(os.environ.get("ZEDQA_LOCK_TIMEOUT", "30"))
    try:
        manager.acquire_many(resources, timeout=timeout)
        _HELD[nodeid] = resources
    except Exception as exc:  # noqa: BLE001
        _emit({"type": "log", "message": f"lock failed for {nodeid}: {exc}"})
        if pytest is not None:
            pytest.fail(f"zedqa: could not acquire resource lock: {exc}", pytrace=False)


def pytest_runtest_teardown(item, nextitem):  # noqa: ANN001, ARG002
    nodeid = _clean_nodeid(item.nodeid)
    resources = _HELD.pop(nodeid, None)
    if resources:
        manager = _get_lock_manager()
        if manager is not None:
            manager.release_many(resources)


def pytest_runtest_logstart(nodeid, location):  # noqa: ANN001, ARG002
    # start event was emitted in runtest_setup; this hook keeps ordering with reports
    return None


def pytest_runtest_logreport(report):  # noqa: ANN001
    # In distributed mode only workers report, to avoid duplicate events.
    if _DISTRIBUTED and not os.environ.get("PYTEST_XDIST_WORKER"):
        return
    nodeid = _clean_nodeid(report.nodeid)
    payload = {
        "type": "report",
        "nodeid": nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "duration": report.duration,
        "worker": _worker(),
        "attempt": _ATTEMPTS.get(nodeid, 1),
        "skipped": bool(getattr(report, "skipped", False)),
        "wasxfail": getattr(report, "wasxfail", None) is not None,
    }
    if report.outcome in ("failed", "rerun") and not getattr(report, "wasxfail", None):
        longrepr = str(report.longrepr)
        payload["longrepr"] = longrepr[-8000:]
        payload["error_type"] = _error_type(longrepr)
    _emit(payload)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # noqa: ANN001
    outcome = yield
    report = outcome.get_result()
    # pytest-rerunfailures marks a to-be-retried failure as outcome="rerun".
    if report.when == "call" and report.outcome in ("failed", "rerun"):
        _capture_evidence(item, report)


def _error_type(longrepr: str) -> str:
    for pat in (r"(\w*Error):", r"(\w*Exception):", r"(Failed):", r"(TimeoutError)"):
        m = re.search(pat, longrepr)
        if m:
            return m.group(1)
    return "Failure"


def _capture_evidence(item, report) -> None:  # noqa: ANN001
    if not _EVIDENCE_DIR:
        return
    page = getattr(item, "funcargs", {}).get("page")
    if page is None:
        return
    nodeid = _clean_nodeid(report.nodeid)
    attempt = _ATTEMPTS.get(nodeid, 1)
    name = f"{_safe_name(nodeid)}-a{attempt}.png"
    shots = Path(_EVIDENCE_DIR) / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / name
    try:
        page.screenshot(path=str(path))
    except Exception as exc:  # noqa: BLE001 - evidence must never mask a test failure
        _emit({"type": "log", "message": f"screenshot failed for {nodeid}: {exc}"})
        return
    _emit(
        {
            "type": "evidence",
            "nodeid": nodeid,
            "kind": "screenshot",
            "path": str(path),
            "attempt": attempt,
            "worker": _worker(),
        }
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    _emit({"type": "session_finish", "exitstatus": int(exitstatus), "worker": _worker()})
