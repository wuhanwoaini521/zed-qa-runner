"""End-to-end runner integration tests using real pytest subprocesses."""

from __future__ import annotations

import threading
from pathlib import Path

from zedqa.core.models import CaseStatus, RunConfig, RunState, ScheduleStrategy
from zedqa.scheduler.planner import measure_group_overlap


def _cases(discovery, predicate):  # noqa: ANN001
    return [c for c in discovery.cases if predicate(c.nodeid)]


def test_only_selected_cases_run(demo_service, demo_discovery, demo_run) -> None:
    selection = [
        "tests/test_login.py::TestLogin::test_success",
        "tests/test_login.py::TestLogin::test_logout",
        "tests/test_cart.py::TestCart::test_add",
    ]
    manifest = demo_run(selection=selection, strategy=ScheduleStrategy.CASE, workers=2, reruns=0)
    assert set(manifest.results) == set(selection)
    assert manifest.counts.total == 3
    assert manifest.counts.failed == 0
    assert manifest.state is RunState.PASSED


def test_class_strategy_pins_class_to_one_worker(demo_run, demo_discovery) -> None:
    cases = _cases(demo_discovery, lambda n: "TestLogin" in n or "TestCart" in n)
    manifest = demo_run(cases=cases, strategy=ScheduleStrategy.CLASS, workers=4, reruns=0)
    login_nodes = [n for n in manifest.results if "TestLogin" in n]
    workers = {manifest.results[n].worker for n in login_nodes}
    assert len(workers) == 1, f"same class spread across workers: {workers}"
    # and the deliberate failure is still a failure (no hiding)
    assert manifest.results["tests/test_login.py::TestLogin::test_failure"].status in {
        CaseStatus.FAILED,
        CaseStatus.ERROR,
    }


def test_resource_strategy_mutual_exclusion(demo_run, demo_discovery) -> None:
    manifest = demo_run()  # full demo, resource, 4 workers, reruns=1
    case_worker = {nid: r.worker for nid, r in manifest.results.items()}
    violations = measure_group_overlap(manifest.plan, case_worker)
    assert violations == [], violations
    # account A classes are in one group and therefore one worker
    a_nodes = [n for n in manifest.results if "TestLogin" in n or "TestCart" in n]
    assert len({case_worker[n] for n in a_nodes}) == 1
    # Search (account B) and Checkout (account C) merged transitively via region US
    b_c = [n for n in manifest.results if "TestSearch" in n or "TestCheckout" in n]
    assert len({case_worker[n] for n in b_c}) == 1


def test_flaky_keeps_failure_evidence(demo_run) -> None:
    manifest = demo_run()
    flaky = manifest.results["tests/test_search.py::TestSearch::test_flaky"]
    assert flaky.status is CaseStatus.FLAKY
    assert flaky.attempts == 2
    assert flaky.longrepr and "first attempt fails" in flaky.longrepr


def test_playwright_failure_has_screenshot(demo_run) -> None:
    manifest = demo_run()
    result = manifest.results["tests/test_checkout.py::TestCheckout::test_pw_failure[chromium]"]
    assert result.status in {CaseStatus.FAILED, CaseStatus.ERROR}
    assert result.screenshots, "a failed Playwright test must keep a screenshot"
    assert Path(result.screenshots[0]).exists()


def test_report_generated_despite_failures(demo_run) -> None:
    manifest = demo_run()
    assert manifest.counts.failed >= 1
    assert manifest.report_error is None, manifest.report_error
    assert manifest.allure_report
    assert (Path(manifest.allure_report) / "index.html").exists()


def test_two_runs_do_not_pollute(demo_service, demo_run) -> None:
    a = demo_run(
        selection=["tests/test_login.py::TestLogin::test_success"],
        strategy=ScheduleStrategy.CASE,
        workers=1,
        reruns=0,
    )
    b = demo_run(
        selection=["tests/test_cart.py::TestCart::test_add"],
        strategy=ScheduleStrategy.CASE,
        workers=1,
        reruns=0,
    )
    assert a.run_id != b.run_id
    assert a.allure_results != b.allure_results
    assert set(a.results) == {"tests/test_login.py::TestLogin::test_success"}
    assert set(b.results) == {"tests/test_cart.py::TestCart::test_add"}
    # every allure result file in a run dir belongs to that run only
    a_files = {p.name for p in Path(a.allure_results).iterdir()}
    b_files = {p.name for p in Path(b.allure_results).iterdir()}
    assert a_files.isdisjoint(b_files)


def test_cancellation_releases_resources(demo_service, demo_discovery) -> None:
    cases = _cases(
        demo_discovery,
        lambda n: n.endswith("test_profile") or n.endswith("test_slow"),
    )
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    cfg = RunConfig(
        project_root=str(demo_service.root),
        workers=2,
        strategy=ScheduleStrategy.RESOURCE,
        reruns=0,
    )
    manifest = demo_service.run(cases, cfg, cancel_event=cancel)
    assert manifest.state is RunState.CANCELLED
    # no dangling resource locks
    lock_dir = demo_service.root / demo_service.config.resources.lock_dir
    assert not list(lock_dir.glob("*.lock")), "locks must be released on cancellation"
