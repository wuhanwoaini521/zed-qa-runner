"""Structured data models shared by CLI, TUI, scheduler, runner and reporting.

Everything that crosses a module boundary is a Pydantic model so that CLI, TUI and
JSON persistence all speak the same language.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"


class CaseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    XPASSED = "xpassed"
    FLAKY = "flaky"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    CaseStatus.PASSED,
    CaseStatus.FAILED,
    CaseStatus.ERROR,
    CaseStatus.SKIPPED,
    CaseStatus.XFAILED,
    CaseStatus.XPASSED,
    CaseStatus.FLAKY,
    CaseStatus.CANCELLED,
}


class RunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ScheduleStrategy(StrEnum):
    CASE = "case"  # different cases may run in parallel (xdist --dist=load)
    CLASS = "class"  # same class serial (xdist --dist=loadscope) - default
    FILE = "file"  # same file serial (xdist --dist=loadfile)
    RESOURCE = "resource"  # conflict-graph grouping (xdist --dist=loadgroup + hook)


class TestCase(BaseModel):
    """A single collected pytest item. nodeid is the source of truth for execution."""

    __test__ = False  # keep pytest from collecting this model as a test class

    nodeid: str
    name: str
    file: str  # path relative to project root, forward slashes
    line: int | None = None
    cls: str | None = None
    function: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    param_id: str | None = None
    markers: list[str] = Field(default_factory=list)

    @property
    def class_key(self) -> str:
        return f"{self.file}::{self.cls}" if self.cls else self.file

    @property
    def file_key(self) -> str:
        return self.file

    def has_marker(self, marker: str) -> bool:
        return marker in self.markers


class CollectionError(BaseModel):
    file: str | None = None
    message: str


class DiscoveryResult(BaseModel):
    root: str
    interpreter: str
    cases: list[TestCase] = Field(default_factory=list)
    errors: list[CollectionError] = Field(default_factory=list)
    duration: float = 0.0
    command: list[str] = Field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return sorted({c.file for c in self.cases})

    @property
    def classes(self) -> list[str]:
        return sorted({c.cls for c in self.cases if c.cls})


class ResourceRule(BaseModel):
    """Maps a glob pattern (class key, file, or nodeid) to shared resource ids."""

    match: str
    resources: list[str] = Field(default_factory=list)


class ScheduleGroup(BaseModel):
    name: str
    case_nodeids: list[str]
    reason: str
    resources: list[str] = Field(default_factory=list)


class SchedulePlan(BaseModel):
    strategy: ScheduleStrategy
    groups: list[ScheduleGroup] = Field(default_factory=list)
    workers: int = 1
    selected_count: int = 0
    class_count: int = 0
    resource_groups: list[list[str]] = Field(default_factory=list)
    max_parallelism: int = 1
    notes: list[str] = Field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"selected cases : {self.selected_count}",
            f"classes        : {self.class_count}",
            f"strategy       : {self.strategy.value}",
            f"workers        : {self.workers} (effective parallelism {self.max_parallelism})",
            f"groups         : {len(self.groups)}",
        ]
        for g in self.groups:
            res = f"  resources={','.join(g.resources)}" if g.resources else ""
            lines.append(f"  - {g.name}: {len(g.case_nodeids)} case(s) [{g.reason}]{res}")
        lines.extend(f"note: {n}" for n in self.notes)
        return lines


class RunConfig(BaseModel):
    project_root: str
    selection: list[str] = Field(default_factory=list)
    workers: int = 1
    strategy: ScheduleStrategy = ScheduleStrategy.CLASS
    markers: list[str] = Field(default_factory=list)
    keyword: str | None = None
    headless: bool = True
    reruns: int = 0
    timeout: float | None = None
    browser: str = "chromium"
    extra_args: list[str] = Field(default_factory=list)
    interpreter: str | None = None
    open_report: bool = False
    fail_fast: bool = False


class CaseResult(BaseModel):
    nodeid: str
    status: CaseStatus = CaseStatus.PENDING
    worker: str | None = None
    group: str | None = None
    attempts: int = 0
    duration: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    error_type: str | None = None
    longrepr: str | None = None
    failure_phase: str | None = None
    screenshots: list[str] = Field(default_factory=list)
    traces: list[str] = Field(default_factory=list)
    videos: list[str] = Field(default_factory=list)

    @property
    def is_failure(self) -> bool:
        return self.status in {CaseStatus.FAILED, CaseStatus.ERROR}


class RunCounts(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    error: int = 0
    skipped: int = 0
    flaky: int = 0
    pending: int = 0

    def add(self, status: CaseStatus) -> None:
        if status == CaseStatus.PASSED:
            self.passed += 1
        elif status == CaseStatus.FAILED:
            self.failed += 1
        elif status == CaseStatus.ERROR:
            self.error += 1
        elif status == CaseStatus.SKIPPED:
            self.skipped += 1
        elif status == CaseStatus.FLAKY:
            self.flaky += 1
        else:
            self.pending += 1


class RunManifest(BaseModel):
    run_id: str
    project_root: str
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    state: RunState = RunState.PENDING
    config: RunConfig
    plan: SchedulePlan | None = None
    counts: RunCounts = Field(default_factory=RunCounts)
    results: dict[str, CaseResult] = Field(default_factory=dict)
    exit_code: int | None = None
    command: list[str] = Field(default_factory=list)
    report_dir: str | None = None
    allure_results: str | None = None
    allure_report: str | None = None
    junit_xml: str | None = None
    pytest_log: str | None = None
    report_error: str | None = None
    run_dir: str | None = None
    duration: float = 0.0

    @property
    def failed_nodeids(self) -> list[str]:
        return [nid for nid, r in self.results.items() if r.is_failure]


class RunSummary(BaseModel):
    run_id: str
    state: RunState
    created_at: datetime
    total: int
    passed: int
    failed: int
    flaky: int
    duration: float
    run_dir: str
    report_exists: bool = False


class Suite(BaseModel):
    name: str
    nodeids: list[str] = Field(default_factory=list)
    # dynamic rule based suites: {"file": "tests/x.py", "class": "TestX", "marker": "smoke", "keyword": "..."}
    filter: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ResourceSection(BaseModel):
    rules: list[ResourceRule] = Field(default_factory=list)
    lock_dir: str = ".qa-runs/.locks"
    lock_timeout: float = 30.0
    stale_after: float = 600.0


class SchedulingSection(BaseModel):
    default_workers: int = 2
    default_strategy: ScheduleStrategy = ScheduleStrategy.CLASS
    include_class_in_resource_mode: bool = True


class BrowserSection(BaseModel):
    browser: str = "chromium"
    headless: bool = True
    channel: str | None = None


class ReportingSection(BaseModel):
    runs_dir: str = ".qa-runs"
    open_report: bool = False


class ZedqaConfig(BaseModel):
    version: int = 1
    scheduling: SchedulingSection = Field(default_factory=SchedulingSection)
    resources: ResourceSection = Field(default_factory=ResourceSection)
    browser: BrowserSection = Field(default_factory=BrowserSection)
    reporting: ReportingSection = Field(default_factory=ReportingSection)
    suites: dict[str, Suite] = Field(default_factory=dict)


class RunEvent(BaseModel):
    """Structured event pushed from the runner to any UI."""

    type: str  # start | report | evidence | session_finish | log | run_state
    run_id: str = "unknown"
    nodeid: str | None = None
    when: str | None = None
    outcome: str | None = None
    worker: str | None = None
    duration: float = 0.0
    attempt: int = 0
    status: CaseStatus | None = None
    message: str | None = None
    error_type: str | None = None
    kind: str | None = None
    path: str | None = None
    timestamp: float = Field(default_factory=time.time)

    @classmethod
    def from_raw(cls, raw: dict) -> RunEvent:
        data = dict(raw)
        data.setdefault("type", "log")
        if "longrepr" in data and "message" not in data:
            data["message"] = data.pop("longrepr")
        return cls.model_validate(data)


class DoctorCheck(BaseModel):
    name: str
    ok: bool
    detail: str
    required: bool = False
    hint: str | None = None
