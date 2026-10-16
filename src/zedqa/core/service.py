"""ProjectService: the single business layer used by both CLI and TUI.

No execution logic lives in the UI. The TUI and the CLI call the same methods.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from zedqa.core.config import load_config
from zedqa.core.interpreter import resolve_interpreter
from zedqa.core.models import (
    CaseResult,
    CaseStatus,
    DiscoveryResult,
    RunConfig,
    RunCounts,
    RunEvent,
    RunManifest,
    RunState,
    RunSummary,
    SchedulePlan,
    ScheduleStrategy,
    Suite,
    TestCase,
    new_run_id,
    utcnow,
)
from zedqa.core.paths import ensure_run_dirs
from zedqa.discovery.collector import build_env
from zedqa.discovery.collector import discover as _discover
from zedqa.reporting import allure as allure_mod
from zedqa.reporting import store
from zedqa.runner.aggregator import RunAggregator
from zedqa.runner.executor import (
    build_args,
    detect_capabilities,
    stream_run,
    write_json_map,
)
from zedqa.scheduler.planner import group_map as plan_group_map
from zedqa.scheduler.planner import plan_schedule, resolve_resources

EventCallback = Callable[[RunEvent], None]


class ProjectService:
    def __init__(self, root: Path | str, interpreter: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.config = load_config(self.root)
        self.interpreter = resolve_interpreter(self.root, interpreter)

    # ---------------------------------------------------------------- discovery
    def discover(
        self,
        markers: list[str] | None = None,
        keyword: str | None = None,
        extra_args: list[str] | None = None,
    ) -> DiscoveryResult:
        result = _discover(self.root, self.interpreter, markers, keyword, extra_args)
        self.interpreter = result.interpreter
        return result

    # ---------------------------------------------------------------- planning
    def plan(self, cases: list[TestCase], run_config: RunConfig) -> SchedulePlan:
        return plan_schedule(
            cases,
            run_config.strategy,
            run_config.workers,
            self.config.resources.rules,
            self.config.scheduling.include_class_in_resource_mode,
        )

    def resource_map(self, cases: list[TestCase]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for c in cases:
            res = resolve_resources(c, self.config.resources.rules)
            if res:
                out[c.nodeid] = res
        return out

    # -------------------------------------------------------------------- runs
    def run(
        self,
        cases: list[TestCase],
        run_config: RunConfig,
        on_event: EventCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RunManifest:
        if not cases:
            raise ValueError("no test cases selected")
        run_config.project_root = str(self.root)
        run_config.interpreter = self.interpreter
        run_config.selection = [c.nodeid for c in cases]
        run_id = new_run_id()
        plan = self.plan(cases, run_config)
        manifest = RunManifest(
            run_id=run_id,
            project_root=str(self.root),
            config=run_config,
            plan=plan,
            state=RunState.RUNNING,
            started_at=utcnow(),
        )
        dirs = ensure_run_dirs(self.root, run_id, self.config.reporting.runs_dir)
        run_dir = dirs["base"]
        manifest.run_dir = str(run_dir)
        manifest.allure_results = str(dirs["allure_results"])
        manifest.junit_xml = str(run_dir / "junit.xml")
        manifest.pytest_log = str(run_dir / "logs" / "pytest.log")

        selection = list(run_config.selection)
        gmap = plan_group_map(plan) if run_config.strategy == ScheduleStrategy.RESOURCE else {}
        rmap = self.resource_map(cases)

        group_file = run_dir / "group-map.json"
        resource_file = run_dir / "resource-map.json"
        write_json_map(group_file, gmap)
        write_json_map(resource_file, rmap)

        event_file = run_dir / "events.jsonl"
        env = build_env(
            {
                "ZEDQA_RUN_ID": run_id,
                "ZEDQA_EVENT_FILE": str(event_file),
                "ZEDQA_GROUP_MAP": str(group_file),
                "ZEDQA_RESOURCE_MAP": str(resource_file),
                "ZEDQA_RESOURCE_LOCK_DIR": str(self.root / self.config.resources.lock_dir),
                "ZEDQA_LOCK_TIMEOUT": str(self.config.resources.lock_timeout),
                "ZEDQA_EVIDENCE_DIR": str(run_dir),
            }
        )

        caps = detect_capabilities(self.interpreter)
        uses_pw = any(c.has_marker("playwright") for c in cases)
        args = build_args(manifest, caps, run_dir, uses_pw)
        manifest.command = args

        aggregator = RunAggregator(selection, {nid: g for nid, g in gmap.items()})
        store.save_manifest(self.root, manifest, self.config.reporting.runs_dir)

        def handle(event: RunEvent) -> None:
            aggregator.apply(event)
            manifest.results = aggregator.results
            manifest.counts = aggregator.counts()
            store.save_manifest(self.root, manifest, self.config.reporting.runs_dir)
            if on_event:
                on_event(event)

        exit_code = stream_run(
            self.root,
            self.interpreter,
            args,
            env,
            run_dir,
            cancel_event=cancel_event,
            on_event=handle,
        )
        manifest.exit_code = exit_code
        manifest.results = aggregator.results
        manifest.counts = aggregator.counts()
        if cancel_event is not None and cancel_event.is_set():
            for res in manifest.results.values():
                if res.status in {CaseStatus.PENDING, CaseStatus.RUNNING}:
                    res.status = CaseStatus.CANCELLED
            manifest.counts = aggregator.counts()
            manifest.state = RunState.CANCELLED
        else:
            # A case with no report means its worker died or was lost: surface it
            # as an error instead of silently reporting a green run.
            lost = [
                r
                for r in manifest.results.values()
                if r.status in {CaseStatus.PENDING, CaseStatus.RUNNING}
            ]
            for res in lost:
                res.status = CaseStatus.ERROR
                res.error = res.error or "no result reported (worker lost?)"
            if lost:
                manifest.counts = aggregator.counts()
            if manifest.counts.failed or manifest.counts.error:
                manifest.state = RunState.FAILED
            elif manifest.counts.total == 0:
                manifest.state = RunState.ERROR
            elif exit_code not in (0,):
                manifest.state = RunState.FAILED
            else:
                manifest.state = RunState.PASSED
        manifest.finished_at = utcnow()
        manifest.duration = (
            manifest.finished_at - (manifest.started_at or manifest.finished_at)
        ).total_seconds()

        _collect_playwright_artifacts(run_dir, manifest.results)

        # Report generation is a separate status: test failures never block it.
        ok, detail = allure_mod.generate_report(
            Path(manifest.allure_results), dirs["allure_report"]
        )
        if ok:
            manifest.allure_report = str(dirs["allure_report"])
        else:
            manifest.report_error = detail

        store.save_manifest(self.root, manifest, self.config.reporting.runs_dir)

        if run_config.open_report and manifest.allure_report:
            allure_mod.open_report(Path(manifest.allure_report))
        return manifest

    # --------------------------------------------------------------- runners
    def load_run(self, run_id: str) -> RunManifest:
        return store.load_manifest(self.root, run_id, self.config.reporting.runs_dir)

    def list_runs(self) -> list[RunSummary]:
        return store.list_runs(self.root, self.config.reporting.runs_dir)

    def latest_run_id(self) -> str | None:
        return store.latest_run_id(self.root, self.config.reporting.runs_dir)

    def failed_nodeids(self, run_id: str) -> list[str]:
        manifest = self.load_run(run_id)
        return manifest.failed_nodeids

    def rerun(
        self,
        run_id: str,
        nodeids: list[str] | None = None,
        on_event: EventCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RunManifest:
        old = self.load_run(run_id)
        targets = nodeids or old.failed_nodeids
        if not targets:
            raise ValueError(f"run {run_id} has no failed cases to rerun")
        discovery = self.discover()
        lookup = {c.nodeid: c for c in discovery.cases}
        cases: list[TestCase] = []
        for nid in targets:
            if nid in lookup:
                cases.append(lookup[nid])
            else:
                # node may be gone; keep a synthetic case so the user sees an error
                # rather than silently running a different test.
                cases.append(
                    TestCase(nodeid=nid, name=nid.split("::")[-1], file=nid.split("::")[0])
                )
        cfg = old.config.model_copy(update={"selection": targets, "open_report": False})
        return self.run(cases, cfg, on_event=on_event, cancel_event=cancel_event)

    # -------------------------------------------------------------- reporting
    def generate_report(self, run_id: str, open_after: bool = False) -> tuple[bool, str]:
        manifest = self.load_run(run_id)
        if not manifest.allure_results:
            return False, "run has no allure results"
        report_dir = Path(manifest.run_dir or "") / "allure-report"
        ok, detail = allure_mod.generate_report(Path(manifest.allure_results), report_dir)
        if ok:
            manifest.allure_report = str(report_dir)
            if open_after:
                allure_mod.open_report(report_dir)
        else:
            manifest.report_error = detail
        store.save_manifest(self.root, manifest, self.config.reporting.runs_dir)
        return ok, detail

    def open_report(self, run_id: str) -> tuple[bool, str]:
        manifest = self.load_run(run_id)
        if manifest.allure_report and Path(manifest.allure_report).exists():
            return allure_mod.open_report(Path(manifest.allure_report))
        return self.generate_report(run_id, open_after=True)

    # ----------------------------------------------------------------- suites
    def save_suite(self, suite: Suite) -> None:
        self.config.suites[suite.name] = suite
        from zedqa.core.config import save_config

        save_config(self.root, self.config)

    def delete_suite(self, name: str) -> bool:
        if name in self.config.suites:
            del self.config.suites[name]
            from zedqa.core.config import save_config

            save_config(self.root, self.config)
            return True
        return False

    def resolve_suite(
        self, suite: Suite, discovery: DiscoveryResult
    ) -> tuple[list[TestCase], list[str]]:
        """Return (resolved cases, missing nodeids). Missing nodes are surfaced, not hidden."""
        lookup = {c.nodeid: c for c in discovery.cases}
        if suite.nodeids:
            cases = [lookup[n] for n in suite.nodeids if n in lookup]
            missing = [n for n in suite.nodeids if n not in lookup]
            return cases, missing
        f = suite.filter
        cases = []
        for c in discovery.cases:
            if f.get("file") and c.file != f["file"]:
                continue
            if f.get("class") and c.cls != f["class"]:
                continue
            if f.get("marker") and not c.has_marker(f["marker"]):
                continue
            if f.get("keyword") and f["keyword"] not in c.nodeid:
                continue
            cases.append(c)
        return cases, []


def _collect_playwright_artifacts(run_dir: Path, results: dict[str, CaseResult]) -> None:
    """Attach pytest-playwright trace/video files to their test by name match."""
    base = run_dir / "playwright-artifacts"
    if not base.exists():
        return

    def norm(text: str) -> str:
        return "".join(ch.lower() for ch in text if ch.isalnum())

    for path in base.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".zip", ".webm", ".mp4"}:
            continue
        rel = norm(str(path.relative_to(base)))
        for nid, res in results.items():
            token = norm(nid.split("::")[-1] + nid.split("/")[-1])
            if token and token[:20] in rel:
                target = res.traces if suffix == ".zip" else res.videos
                target.append(str(path))
                break


def counts_from_results(results: dict[str, CaseResult]) -> RunCounts:
    c = RunCounts(total=len(results))
    for r in results.values():
        c.add(r.status)
    return c
