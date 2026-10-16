"""Aggregate raw pytest JSONL events into CaseResult state.

The same aggregator is used by the live UI and by post-run persistence, so the
displayed state and the saved manifest never diverge.
"""

from __future__ import annotations

from zedqa.core.models import CaseResult, CaseStatus, RunCounts, RunEvent


class RunAggregator:
    def __init__(self, nodeids: list[str], groups: dict[str, str] | None = None) -> None:
        self.results: dict[str, CaseResult] = {
            nid: CaseResult(nodeid=nid, group=(groups or {}).get(nid)) for nid in nodeids
        }
        self._first_failure: dict[str, dict] = {}

    def apply(self, event: RunEvent) -> None:
        if event.type == "start":
            res = self._get(event.nodeid)
            if res:
                res.status = CaseStatus.RUNNING
                res.worker = event.worker or res.worker
                res.attempts = max(res.attempts, event.attempt or 1)
                if res.started_at is None:
                    from zedqa.core.models import utcnow

                    res.started_at = utcnow()
        elif event.type == "report":
            self._apply_report(event)
        elif event.type == "evidence":
            res = self._get(event.nodeid)
            if res and event.path:
                if event.kind == "trace":
                    res.traces.append(event.path)
                elif event.kind == "video":
                    res.videos.append(event.path)
                else:
                    res.screenshots.append(event.path)
        return

    def _apply_report(self, event: RunEvent) -> None:
        res = self._get(event.nodeid)
        if res is None:
            return
        nodeid = event.nodeid
        if nodeid is None:
            return
        res.worker = event.worker or res.worker
        res.attempts = max(res.attempts, event.attempt or 1)
        res.duration += event.duration or 0.0
        attempt = event.attempt or 1

        if event.outcome in ("failed", "rerun"):
            # Keep the FIRST failure text/evidence even if a rerun later passes.
            # pytest-rerunfailures reports a retried failure as outcome="rerun".
            if nodeid not in self._first_failure:
                self._first_failure[nodeid] = {
                    "longrepr": event.message,
                    "phase": event.when,
                }
            if event.error_type:
                res.error_type = res.error_type or event.error_type
            if event.when == "call":
                res.status = CaseStatus.FAILED
            elif event.when == "setup":
                res.status = CaseStatus.ERROR
            elif event.when == "teardown" and res.status not in {
                CaseStatus.FAILED,
                CaseStatus.ERROR,
            }:
                res.status = CaseStatus.ERROR
            if res.longrepr is None or attempt == 1:
                res.longrepr = event.message or res.longrepr
                res.failure_phase = event.when
        elif event.outcome == "passed":
            if event.when == "call":
                if attempt > 1:
                    res.status = CaseStatus.FLAKY
                elif res.status not in {CaseStatus.FLAKY}:
                    res.status = CaseStatus.PASSED
        elif event.outcome == "skipped":
            if event.when in ("setup", "call") and res.status not in {
                CaseStatus.FAILED,
                CaseStatus.ERROR,
            }:
                res.status = CaseStatus.SKIPPED

        if res.status in {CaseStatus.FAILED, CaseStatus.ERROR, CaseStatus.FLAKY}:
            first = self._first_failure.get(nodeid)
            if first and not res.longrepr:
                res.longrepr = first.get("longrepr")
                res.failure_phase = first.get("phase")

    def _get(self, nodeid: str | None) -> CaseResult | None:
        if nodeid is None:
            return None
        return self.results.get(nodeid)

    def counts(self) -> RunCounts:
        c = RunCounts(total=len(self.results))
        for res in self.results.values():
            c.add(res.status)
        return c
