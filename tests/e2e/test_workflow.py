"""End-to-end acceptance workflow through the shared ProjectService."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from zedqa.core.models import CaseStatus, RunConfig, RunState, ScheduleStrategy
from zedqa.integrations import zed as zed_integration

pytestmark = pytest.mark.e2e


def test_full_workflow_discover_select_run_rerun_report(demo_service, demo_discovery) -> None:
    # 1. select 5 cases across 3 files (acceptance scenario 2)
    selection = [
        "tests/test_login.py::TestLogin::test_success",
        "tests/test_login.py::TestLogin::test_failure",
        "tests/test_cart.py::TestCart::test_add",
        "tests/test_search.py::TestSearch::test_search[JP]",
        "tests/test_search.py::TestSearch::test_locale",
    ]
    cases = [c for c in demo_discovery.cases if c.nodeid in selection]
    assert len(cases) == 5

    # 2. plan preview distinguishes plan from runtime assignment
    plan = demo_service.plan(
        cases,
        RunConfig(
            project_root=str(demo_service.root), workers=4, strategy=ScheduleStrategy.RESOURCE
        ),
    )
    assert plan.selected_count == 5
    assert len(plan.groups) == 2  # account A group + account B group

    # 3. run only those 5
    manifest = demo_service.run(
        cases,
        RunConfig(
            project_root=str(demo_service.root),
            workers=4,
            strategy=ScheduleStrategy.RESOURCE,
            reruns=0,
        ),
    )
    assert set(manifest.results) == set(selection)
    assert manifest.counts.total == 5
    assert manifest.results[selection[1]].status in {CaseStatus.FAILED, CaseStatus.ERROR}

    # 4. failures are inspectable with evidence linkage
    failed = demo_service.failed_nodeids(manifest.run_id)
    assert failed == [selection[1]]

    # 5. report exists even though a test failed
    assert manifest.allure_report and (Path(manifest.allure_report) / "index.html").exists()

    # 6. rerun only the failed case, using the exact nodeid
    rerun = demo_service.rerun(manifest.run_id)
    assert set(rerun.results) == {selection[1]}
    assert rerun.run_id != manifest.run_id

    # 7. run appears in history and can be reopened
    history = {s.run_id for s in demo_service.list_runs()}
    assert manifest.run_id in history and rerun.run_id in history
    assert Path(manifest.allure_report).exists()


def test_zed_task_and_debug_are_consistent(tmp_path: Path) -> None:
    zed_integration.install_tasks(tmp_path)
    zed_integration.install_debug(tmp_path)
    import json

    tasks = json.loads((tmp_path / ".zed" / "tasks.json").read_text())
    labels = {t["label"]: t["command"] for t in tasks}
    # the TUI and file/symbol run tasks go through the same CLI/service
    assert any(cmd == "zedqa ui" for cmd in labels.values())
    assert "zedqa run --file $ZED_RELATIVE_FILE" in labels.values()

    debug = json.loads((tmp_path / ".zed" / "debug.json").read_text())
    for cfg in debug:
        assert cfg["adapter"] == "Debugpy"
        assert "-n0" in cfg["args"], "debug configs must not use xdist"
        assert "no:rerunfailures" in cfg["args"]


def test_worker_crash_does_not_hang(tmp_path: Path) -> None:
    """Acceptance scenario 8: a worker process dying must not hang the run."""
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (tmp_path / "tests" / "test_crash.py").write_text(
        "import os\n"
        "def test_ok_1():\n    assert True\n"
        "def test_crash():\n    os._exit(1)\n"
        "def test_ok_2():\n    assert True\n",
        encoding="utf-8",
    )
    from zedqa.core.service import ProjectService

    svc = ProjectService(tmp_path)
    cases = svc.discover().cases
    started = time.monotonic()
    manifest = svc.run(
        cases, RunConfig(project_root=str(tmp_path), workers=2, strategy=ScheduleStrategy.CASE)
    )
    assert time.monotonic() - started < 120, "run must terminate when a worker dies"
    assert manifest.state in {RunState.FAILED, RunState.ERROR}
    # the crash must be visible, not silently swallowed
    assert manifest.counts.failed + manifest.counts.error >= 1 or manifest.exit_code not in (0,)
