"""Scheduler grouping and conflict-graph tests (pure logic)."""

from __future__ import annotations

import pytest

from zedqa.core.models import (
    ResourceRule,
    ScheduleStrategy,
    TestCase,
)
from zedqa.scheduler.planner import (
    group_map,
    measure_group_overlap,
    plan_schedule,
    resolve_resources,
    xdist_args,
)


def case(nodeid: str, cls: str | None = None, file: str | None = None) -> TestCase:
    f = file or nodeid.split("::")[0]
    return TestCase(nodeid=nodeid, name=nodeid.split("::")[-1], file=f, cls=cls)


@pytest.fixture
def cases() -> list[TestCase]:
    return [
        case("tests/test_login.py::TestLogin::test_a", "TestLogin"),
        case("tests/test_login.py::TestLogin::test_b", "TestLogin"),
        case("tests/test_cart.py::TestCart::test_c", "TestCart"),
        case("tests/test_search.py::TestSearch::test_d", "TestSearch"),
        case("tests/test_search.py::TestSearch::test_e", "TestSearch"),
    ]


RULES = [
    ResourceRule(match="tests/test_login.py::TestLogin", resources=["account:A", "region:JP"]),
    ResourceRule(match="tests/test_cart.py::TestCart", resources=["account:A", "region:JP"]),
    ResourceRule(match="tests/test_search.py::TestSearch", resources=["account:B", "region:US"]),
]


def test_class_strategy_groups_by_class(cases: list[TestCase]) -> None:
    plan = plan_schedule(cases, ScheduleStrategy.CLASS, workers=4)
    by_name = {g.name: g.case_nodeids for g in plan.groups}
    assert len(plan.groups) == 3
    assert sorted(by_name["class:tests_test_login.py__TestLogin"]) == [
        "tests/test_login.py::TestLogin::test_a",
        "tests/test_login.py::TestLogin::test_b",
    ]
    assert plan.max_parallelism == 3


def test_file_strategy_groups_by_file(cases: list[TestCase]) -> None:
    plan = plan_schedule(cases, ScheduleStrategy.FILE, workers=4)
    # test_login and test_search are distinct files; each is one group.
    assert len(plan.groups) == 3
    login = next(g for g in plan.groups if "test_login" in g.name)
    assert len(login.case_nodeids) == 2


def test_case_strategy_one_group_per_case(cases: list[TestCase]) -> None:
    plan = plan_schedule(cases, ScheduleStrategy.CASE, workers=3)
    assert len(plan.groups) == 5
    assert plan.max_parallelism == 3  # clamped by workers


def test_resource_strategy_merges_shared_account(cases: list[TestCase]) -> None:
    plan = plan_schedule(cases, ScheduleStrategy.RESOURCE, workers=4, rules=RULES)
    # Login + Cart share account:A -> merged; Search is separate.
    assert len(plan.groups) == 2
    merged = next(g for g in plan.groups if "account:A" in g.resources)
    assert len(merged.case_nodeids) == 3
    assert plan.max_parallelism == 2


def test_resource_transitive_conflict() -> None:
    # A shares 'x' with B; B shares 'y' with C; A and C share nothing but must merge.
    cases = [
        case("tests/a.py::A::test_1", "A"),
        case("tests/b.py::B::test_1", "B"),
        case("tests/c.py::C::test_1", "C"),
    ]
    rules = [
        ResourceRule(match="tests/a.py::A", resources=["x"]),
        ResourceRule(match="tests/b.py::B", resources=["x", "y"]),
        ResourceRule(match="tests/c.py::C", resources=["y"]),
    ]
    plan = plan_schedule(cases, ScheduleStrategy.RESOURCE, workers=8, rules=rules)
    assert len(plan.groups) == 1, "transitive conflicts must merge into one group"
    assert len(plan.groups[0].case_nodeids) == 3


def test_resource_strategy_without_rules_isolates_by_class(cases: list[TestCase]) -> None:
    plan = plan_schedule(cases, ScheduleStrategy.RESOURCE, workers=4, rules=[])
    # Each class is an implicit resource, so 3 groups.
    assert len(plan.groups) == 3
    assert all(g.resources == [] for g in plan.groups)


def test_resolve_resources_matches_nodeid_class_and_file() -> None:
    c = case("tests/test_login.py::TestLogin::test_a", "TestLogin")
    rules = [
        ResourceRule(match="*TestLogin*", resources=["account:A"]),
        ResourceRule(match="tests/test_login.py", resources=["file:login"]),
        ResourceRule(match="tests/test_login.py::TestLogin::test_a", resources=["case:a"]),
    ]
    assert resolve_resources(c, rules) == ["account:A", "case:a", "file:login"]


def test_workers_clamped_and_empty() -> None:
    plan = plan_schedule([], ScheduleStrategy.CLASS, workers=4)
    assert plan.selected_count == 0 and plan.groups == []
    one = plan_schedule([case("t.py::T::t", "T")], ScheduleStrategy.CLASS, workers=8)
    assert one.max_parallelism == 1


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        (ScheduleStrategy.CASE, ["-n", "4", "--dist", "load"]),
        (ScheduleStrategy.CLASS, ["-n", "4", "--dist", "loadscope"]),
        (ScheduleStrategy.FILE, ["-n", "4", "--dist", "loadfile"]),
        (ScheduleStrategy.RESOURCE, ["-n", "4", "--dist", "loadgroup"]),
    ],
)
def test_xdist_args(strategy: ScheduleStrategy, expected: list[str]) -> None:
    assert xdist_args(strategy, 4) == expected
    assert xdist_args(strategy, 1) == []


def test_group_map_and_overlap_detection(cases: list[TestCase]) -> None:
    plan = plan_schedule(cases, ScheduleStrategy.RESOURCE, workers=2, rules=RULES)
    gmap = group_map(plan)
    assert set(gmap) == {c.nodeid for c in cases}
    # All grouped cases on one worker -> no violation.
    worker = {nid: "gw0" for nid in gmap}
    assert measure_group_overlap(plan, worker) == []
    # Split a group across workers -> violation detected.
    first_group = plan.groups[0]
    worker[first_group.case_nodeids[0]] = "gw1"
    if len(first_group.case_nodeids) > 1:
        worker[first_group.case_nodeids[1]] = "gw2"
        assert measure_group_overlap(plan, worker)
