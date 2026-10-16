"""zedqa Terminal UI.

Layout: Test Explorer on the left, run configuration on the right, live run
monitor at the bottom. All execution goes through ProjectService, so the TUI has
no private execution logic.
"""

from __future__ import annotations

import threading
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    ProgressBar,
    RichLog,
    Static,
)

from zedqa.core.models import (
    CaseStatus,
    DiscoveryResult,
    RunConfig,
    RunEvent,
    RunManifest,
    ScheduleStrategy,
    TestCase,
)
from zedqa.core.service import ProjectService
from zedqa.reporting import allure as allure_mod

SELECTED = "[x]"
UNSELECTED = "[ ]"


class ZedqaTUI(App):
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #explorer { width: 3fr; border: round $primary; }
    #config { width: 1fr; border: round $secondary; padding: 0 1; }
    #monitor { height: 14; border: round $accent; }
    #search { dock: top; }
    #summary { height: auto; padding: 0 1; }
    DataTable { height: 1fr; }
    #progress { height: 1; }
    #runtable { height: 1fr; }
    """

    BINDINGS = [
        Binding("space", "toggle_case", "Toggle"),
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("i", "invert", "Invert"),
        Binding("f", "select_failed", "Failed"),
        Binding("r", "run", "Run"),
        Binding("p", "plan", "Plan"),
        Binding("c", "cycle_strategy", "Strategy"),
        Binding("+", "more_workers", "Workers+"),
        Binding("-", "fewer_workers", "Workers-"),
        Binding("t", "cycle_reruns", "Reruns"),
        Binding("h", "toggle_headless", "Headless"),
        Binding("s", "stop", "Stop"),
        Binding("o", "open_report", "Report"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.svc = ProjectService(root)
        self.discovery: DiscoveryResult | None = None
        self._visible: list[TestCase] = []
        self.selected: set[str] = set()
        self.filter_text = ""
        self.strategy = self.svc.config.scheduling.default_strategy
        self.worker_count = self.svc.config.scheduling.default_workers
        self.reruns = 0
        self.headless = self.svc.config.browser.headless
        self.browser = self.svc.config.browser.browser
        self.cancel_event = threading.Event()
        self.running = False
        self.last_manifest: RunManifest | None = None

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="explorer"):
                yield Input(placeholder="search nodeid, file, class, marker…", id="search")
                yield DataTable(id="tests", cursor_type="row")
            with Vertical(id="config"):
                yield Static("", id="configtext")
        with Vertical(id="monitor"):
            yield Static("", id="summary")
            yield ProgressBar(total=100, show_eta=False, id="progress")
            yield DataTable(id="runtable")
            yield RichLog(id="log", markup=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#tests", DataTable)
        table.add_columns("", "file", "class", "case", "markers")
        runtable = self.query_one("#runtable", DataTable)
        runtable.add_columns("status", "case", "worker", "attempts", "time")
        self._refresh_config()
        self.load_tests()

    # --------------------------------------------------------------- discovery
    @work(thread=True, exclusive=True)
    def load_tests(self) -> None:
        self.call_from_thread(self.log_line, "collecting tests with pytest…")
        try:
            discovery = self.svc.discover()
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.log_line, f"discovery failed: {exc}")
            return
        self.call_from_thread(self._apply_discovery, discovery)

    def _apply_discovery(self, discovery: DiscoveryResult) -> None:
        self.discovery = discovery
        self.log_line(f"collected {len(discovery.cases)} case(s) in {discovery.duration:.2f}s")
        for e in discovery.errors:
            self.log_line(f"COLLECTION ERROR {e.file or ''}: {e.message[:300]}")
        self.rebuild_table()

    # ------------------------------------------------------------------ table
    def rebuild_table(self) -> None:
        table = self.query_one("#tests", DataTable)
        table.clear()
        self._visible = []
        needle = self.filter_text.lower()
        for case in self.discovery.cases if self.discovery else []:
            hay = f"{case.nodeid} {' '.join(case.markers)}".lower()
            if needle and needle not in hay:
                continue
            self._visible.append(case)
            mark = SELECTED if case.nodeid in self.selected else UNSELECTED
            table.add_row(mark, case.file, case.cls or "", case.name, ",".join(case.markers))
        self._refresh_config()

    def _cursor_case(self) -> TestCase | None:
        table = self.query_one("#tests", DataTable)
        if not self._visible or table.cursor_row >= len(self._visible):
            return None
        return self._visible[table.cursor_row]

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.filter_text = event.value
            self.rebuild_table()

    # ------------------------------------------------------------------ config
    def _refresh_config(self) -> None:
        text = (
            f"[b]Selection[/]\n"
            f"  selected: {len(self.selected)}\n"
            f"  visible : {len(self._visible)}\n\n"
            f"[b]Execution[/]\n"
            f"  strategy: {self.strategy.value}\n"
            f"  workers : {self.worker_count}\n"
            f"  reruns  : {self.reruns}\n"
            f"  browser : {self.browser}\n"
            f"  headless: {self.headless}\n\n"
            f"[b]Keys[/]\n"
            f"  space toggle   a all   n none   i invert   f failed\n"
            f"  r run   p plan   s stop   o report\n"
            f"  c strategy   + / - workers   t reruns   h headless"
        )
        self.query_one("#configtext", Static).update(text)

    # ------------------------------------------------------------------ actions
    def action_toggle_case(self) -> None:
        case = self._cursor_case()
        if not case:
            return
        if case.nodeid in self.selected:
            self.selected.discard(case.nodeid)
        else:
            self.selected.add(case.nodeid)
        self.rebuild_table()

    def action_select_all(self) -> None:
        self.selected.update(c.nodeid for c in self._visible)
        self.rebuild_table()

    def action_select_none(self) -> None:
        self.selected.clear()
        self.rebuild_table()

    def action_invert(self) -> None:
        for c in self._visible:
            if c.nodeid in self.selected:
                self.selected.discard(c.nodeid)
            else:
                self.selected.add(c.nodeid)
        self.rebuild_table()

    def action_select_failed(self) -> None:
        rid = self.svc.latest_run_id()
        if not rid:
            self.log_line("no previous run to take failures from")
            return
        failed = set(self.svc.failed_nodeids(rid))
        if not failed:
            self.log_line(f"run {rid} has no failures")
            return
        self.selected = failed
        self.log_line(f"selected {len(failed)} failed case(s) from run {rid}")
        self.rebuild_table()

    def action_cycle_strategy(self) -> None:
        order = list(ScheduleStrategy)
        self.strategy = order[(order.index(self.strategy) + 1) % len(order)]
        self._refresh_config()

    def action_more_workers(self) -> None:
        self.worker_count = min(self.worker_count + 1, 32)
        self._refresh_config()

    def action_fewer_workers(self) -> None:
        self.worker_count = max(1, self.worker_count - 1)
        self._refresh_config()

    def action_cycle_reruns(self) -> None:
        self.reruns = 0 if self.reruns >= 3 else self.reruns + 1
        self._refresh_config()

    def action_toggle_headless(self) -> None:
        self.headless = not self.headless
        self._refresh_config()

    def action_stop(self) -> None:
        if self.running:
            self.cancel_event.set()
            self.log_line("cancellation requested…")

    def action_open_report(self) -> None:
        if self.last_manifest and self.last_manifest.allure_report:
            allure_mod.open_report(Path(self.last_manifest.allure_report))
            self.log_line(f"opening {self.last_manifest.allure_report}")
        else:
            self.log_line("no report available yet")

    def action_plan(self) -> None:
        cases = self._selected_cases()
        if not cases:
            self.log_line("nothing selected")
            return
        plan = self.svc.plan(cases, self._build_config())
        for line in plan.summary_lines():
            self.log_line(line)

    def _selected_cases(self) -> list[TestCase]:
        if not self.discovery:
            return []
        return [c for c in self.discovery.cases if c.nodeid in self.selected]

    def _build_config(self) -> RunConfig:
        return RunConfig(
            project_root=str(self.svc.root),
            selection=sorted(self.selected),
            workers=self.worker_count,
            strategy=self.strategy,
            reruns=self.reruns,
            headless=self.headless,
            browser=self.browser,
        )

    # -------------------------------------------------------------------- run
    def action_run(self) -> None:
        if self.running:
            self.log_line("a run is already in progress")
            return
        cases = self._selected_cases()
        if not cases:
            self.log_line("select at least one case (space), then press r")
            return
        self.cancel_event = threading.Event()
        self.running = True
        self._clear_run_table()
        cfg = self._build_config()
        plan = self.svc.plan(cases, cfg)
        self.log_line(
            f"starting run: {len(cases)} case(s), {len(plan.groups)} group(s), "
            f"{self.worker_count} worker(s)"
        )
        self.run_batch(cases, cfg)

    @work(thread=True, exclusive=True)
    def run_batch(self, cases: list[TestCase], cfg: RunConfig) -> None:
        try:
            manifest = self.svc.run(
                cases,
                cfg,
                on_event=lambda e: self.call_from_thread(self._on_event, e),
                cancel_event=self.cancel_event,
            )
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._on_run_error, exc)
            return
        self.call_from_thread(self._on_run_done, manifest)

    def _on_event(self, event: RunEvent) -> None:
        if event.type == "report" and event.when == "call":
            self._update_run_row(event)
        elif event.type == "start":
            self._update_run_row(event, pending=True)
        elif event.type == "evidence" and event.path:
            self.log_line(f"screenshot: {event.path}")
        elif event.type == "log" and event.message:
            self.log_line(event.message)

    def _clear_run_table(self) -> None:
        self.query_one("#runtable", DataTable).clear()
        self.query_one("#log", RichLog).clear()
        self.query_one("#progress", ProgressBar).update(total=100, progress=0)

    def _update_run_row(self, event: RunEvent, pending: bool = False) -> None:
        table = self.query_one("#runtable", DataTable)
        status = "RUN" if pending else (event.outcome or "").upper()
        nid = event.nodeid
        if nid is None:
            return
        try:
            if nid in table.rows:
                table.update_cell(nid, "status", status, update_width=True)
                table.update_cell(nid, "worker", event.worker or "", update_width=True)
                table.update_cell(nid, "attempts", str(event.attempt), update_width=True)
                if event.duration:
                    table.update_cell(nid, "time", f"{event.duration:.2f}s", update_width=True)
            else:
                table.add_row(
                    status,
                    nid,
                    event.worker or "",
                    str(event.attempt),
                    f"{event.duration:.2f}s",
                    key=nid,
                )
        except Exception:  # noqa: BLE001 - UI must never crash on a stray event
            pass
        if event.type == "report" and event.outcome:
            total = len(self.selected) or 1
            done = len([r for r in table.rows])
            self.query_one("#progress", ProgressBar).update(total=total, progress=done)

    def _on_run_done(self, manifest: RunManifest) -> None:
        self.running = False
        self.last_manifest = manifest
        c = manifest.counts
        self.query_one("#summary", Static).update(
            f"Run {manifest.run_id} — [b]{manifest.state.value.upper()}[/]  "
            f"passed={c.passed} failed={c.failed + c.error} flaky={c.flaky} "
            f"skipped={c.skipped}  {manifest.duration:.1f}s"
        )
        self.log_line(f"run {manifest.run_id} finished: {manifest.state.value}")
        if manifest.allure_report:
            self.log_line(f"allure report: {manifest.allure_report} (press o to open)")
        if manifest.report_error:
            self.log_line(f"REPORT ERROR: {manifest.report_error}")
        for nid, r in manifest.results.items():
            if r.status in {CaseStatus.FAILED, CaseStatus.ERROR}:
                self.log_line(f"FAILED {nid} [{r.error_type}] worker={r.worker}")

    def _on_run_error(self, exc: Exception) -> None:
        self.running = False
        self.log_line(f"run error: {exc}")

    # ------------------------------------------------------------------- util
    def log_line(self, message: str) -> None:
        try:
            self.query_one("#log", RichLog).write(message)
        except Exception:  # noqa: BLE001
            pass
