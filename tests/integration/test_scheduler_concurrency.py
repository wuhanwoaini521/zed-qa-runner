"""Prove resource mutual exclusion at runtime, not just on the plan diagram.

Two classes sharing the same account must never have overlapping execution
intervals, while classes on different accounts may overlap.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from zedqa.core.models import RunConfig, ScheduleStrategy
from zedqa.core.service import ProjectService

TEST_FILE = """
import json, os, time
from pathlib import Path

def hold(resource, seconds=0.4):
    log = Path(os.environ["ZEDQA_TIMELINE"])
    with log.open("a") as fh:
        fh.write(json.dumps({"r": resource, "e": "start", "t": time.time()}) + "\\n")
    time.sleep(seconds)
    with log.open("a") as fh:
        fh.write(json.dumps({"r": resource, "e": "end", "t": time.time()}) + "\\n")

class TestA:
    def test_a1(self):
        hold("account:A")

    def test_a2(self):
        hold("account:A")

class TestB:
    def test_b1(self):
        hold("account:A", 0.3)

class TestC:
    def test_c1(self):
        hold("account:B")

class TestD:
    def test_d1(self):
        hold("account:B")
"""

ZEDQA_TOML = """
version = 1
[scheduling]
default_workers = 4
default_strategy = "resource"

[[resources.rules]]
match = "tests/test_conflict.py::TestA"
resources = ["account:A"]
[[resources.rules]]
match = "tests/test_conflict.py::TestB"
resources = ["account:A"]
[[resources.rules]]
match = "tests/test_conflict.py::TestC"
resources = ["account:B"]
[[resources.rules]]
match = "tests/test_conflict.py::TestD"
resources = ["account:B"]
"""


def _make_project(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (root / "tests" / "test_conflict.py").write_text(TEST_FILE, encoding="utf-8")
    (root / "zedqa.toml").write_text(ZEDQA_TOML, encoding="utf-8")


def _intervals(timeline: Path) -> dict[str, list[tuple[float, float]]]:
    events: dict[str, list[dict]] = {}
    for line in timeline.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        events.setdefault(rec["r"], []).append(rec)
    out: dict[str, list[tuple[float, float]]] = {}
    for resource, recs in events.items():
        starts = [r["t"] for r in recs if r["e"] == "start"]
        ends = [r["t"] for r in recs if r["e"] == "end"]
        out[resource] = sorted(zip(sorted(starts), sorted(ends), strict=False))
    return out


def test_same_account_never_overlaps(tmp_path: Path) -> None:
    _make_project(tmp_path)
    timeline = tmp_path / "timeline.jsonl"
    os.environ["ZEDQA_TIMELINE"] = str(timeline)

    svc = ProjectService(tmp_path)
    discovery = svc.discover()
    assert len(discovery.cases) == 5
    manifest = svc.run(
        discovery.cases,
        RunConfig(project_root=str(tmp_path), workers=4, strategy=ScheduleStrategy.RESOURCE),
    )
    assert manifest.counts.failed == 0, [
        (n, r.longrepr) for n, r in manifest.results.items() if r.is_failure
    ]

    intervals = _intervals(timeline)
    assert set(intervals) == {"account:A", "account:B"}
    for resource, spans in intervals.items():
        for i in range(len(spans) - 1):
            gap = spans[i + 1][0] - spans[i][1]
            assert gap >= -0.05, f"{resource} overlapped: {spans[i]} and {spans[i + 1]}"

    # different accounts are allowed to run in parallel: prove concurrency happened
    a_span = (min(s for s, _ in intervals["account:A"]), max(e for _, e in intervals["account:A"]))
    b_span = (min(s for s, _ in intervals["account:B"]), max(e for _, e in intervals["account:B"]))
    overlap = min(a_span[1], b_span[1]) - max(a_span[0], b_span[0])
    assert overlap > 0, "different accounts should have overlapped"


def test_nodeid_with_spaces_and_brackets(tmp_path: Path) -> None:
    spaced = tmp_path / "tests" / "with space"
    spaced.mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (spaced / "test_space.py").write_text(
        "import pytest\n\n@pytest.mark.parametrize('value', ['a b', 'c[d]'])\n"
        "def test_spaced(value):\n    assert value\n",
        encoding="utf-8",
    )
    svc = ProjectService(tmp_path)
    discovery = svc.discover()
    ids = sorted(c.nodeid for c in discovery.cases)
    assert any("a b" in n for n in ids) and any("c[d]" in n for n in ids)

    manifest = svc.run(
        discovery.cases,
        RunConfig(project_root=str(tmp_path), workers=1, strategy=ScheduleStrategy.CASE),
    )
    # argv must be a list; the full nodeid with spaces/brackets ran as one item
    assert set(manifest.results) == set(ids)
    assert manifest.counts.passed == 2
    assert all(isinstance(a, str) for a in manifest.command)
