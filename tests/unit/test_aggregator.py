"""Run event aggregation tests: status, flaky handling, evidence."""

from __future__ import annotations

from zedqa.core.models import CaseStatus, RunEvent
from zedqa.runner.aggregator import RunAggregator


def ev(**kw) -> RunEvent:
    kw.setdefault("type", "report")
    return RunEvent.from_raw(kw)


def test_passed_and_failed() -> None:
    agg = RunAggregator(["a.py::T::t1", "a.py::T::t2"])
    agg.apply(ev(nodeid="a.py::T::t1", when="call", outcome="passed", duration=0.5))
    agg.apply(
        ev(
            nodeid="a.py::T::t2",
            when="call",
            outcome="failed",
            attempt=1,
            longrepr="AssertionError: boom",
            error_type="AssertionError",
        )
    )
    assert agg.results["a.py::T::t1"].status is CaseStatus.PASSED
    assert agg.results["a.py::T::t2"].status is CaseStatus.FAILED
    assert agg.results["a.py::T::t2"].error_type == "AssertionError"
    counts = agg.counts()
    assert (counts.passed, counts.failed) == (1, 1)


def test_flaky_keeps_first_failure() -> None:
    agg = RunAggregator(["a.py::T::t"])
    agg.apply(ev(type="start", nodeid="a.py::T::t", attempt=1, worker="gw0"))
    agg.apply(
        ev(
            nodeid="a.py::T::t",
            when="call",
            outcome="failed",
            attempt=1,
            longrepr="AssertionError: first",
            error_type="AssertionError",
        )
    )
    agg.apply(ev(type="start", nodeid="a.py::T::t", attempt=2, worker="gw0"))
    agg.apply(ev(nodeid="a.py::T::t", when="call", outcome="passed", attempt=2))
    r = agg.results["a.py::T::t"]
    assert r.status is CaseStatus.FLAKY
    assert r.longrepr == "AssertionError: first", "original failure evidence must survive"
    assert r.attempts == 2
    assert agg.counts().flaky == 1


def test_setup_failure_is_error_and_skip() -> None:
    agg = RunAggregator(["a.py::T::err", "a.py::T::skip"])
    agg.apply(
        ev(
            nodeid="a.py::T::err",
            when="setup",
            outcome="failed",
            attempt=1,
            longrepr="fixture error",
        )
    )
    agg.apply(ev(nodeid="a.py::T::skip", when="setup", outcome="skipped", attempt=1))
    assert agg.results["a.py::T::err"].status is CaseStatus.ERROR
    assert agg.results["a.py::T::skip"].status is CaseStatus.SKIPPED


def test_evidence_attached_to_attempt() -> None:
    agg = RunAggregator(["a.py::T::t"])
    agg.apply(ev(nodeid="a.py::T::t", when="call", outcome="failed", attempt=1))
    agg.apply(
        ev(type="evidence", nodeid="a.py::T::t", kind="screenshot", path="/x/shot.png", attempt=1)
    )
    agg.apply(
        ev(type="evidence", nodeid="a.py::T::t", kind="trace", path="/x/trace.zip", attempt=1)
    )
    r = agg.results["a.py::T::t"]
    assert r.screenshots == ["/x/shot.png"]
    assert r.traces == ["/x/trace.zip"]


def test_unknown_nodeid_is_ignored() -> None:
    agg = RunAggregator(["a.py::T::t"])
    agg.apply(ev(nodeid="ghost.py::T::x", when="call", outcome="passed"))
    assert agg.counts().passed == 0
    assert agg.counts().total == 1
