"""Schedule planning: turns a selection of cases into execution groups.

Pure, dependency-free logic so it can be unit tested without running pytest.

Strategies:
  case     -> xdist --dist=load      (any case parallel)
  class    -> xdist --dist=loadscope (same module+class serial)
  file     -> xdist --dist=loadfile  (same file serial)
  resource -> xdist --dist=loadgroup + injected xdist_group markers computed
              from a conflict graph (union-find) over shared resources and classes.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict

from zedqa.core.models import (
    ResourceRule,
    ScheduleGroup,
    SchedulePlan,
    ScheduleStrategy,
    TestCase,
)


def resolve_resources(case: TestCase, rules: list[ResourceRule]) -> list[str]:
    """Resources for a case, matched against nodeid / class key / file via globs."""
    targets = [case.nodeid, case.class_key, case.file]
    out: set[str] = set()
    for rule in rules:
        if any(fnmatch.fnmatch(t, rule.match) for t in targets):
            out.update(rule.resources)
    return sorted(out)


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # deterministic: smaller root wins
            lo, hi = sorted((ra, rb))
            self.parent[hi] = lo


def _group_name(strategy: ScheduleStrategy, key: str) -> str:
    slug = key.replace("/", "_").replace("\\", "_").replace("::", "__").replace(" ", "_")
    return f"{strategy.value}:{slug}"


def plan_schedule(
    cases: list[TestCase],
    strategy: ScheduleStrategy,
    workers: int,
    rules: list[ResourceRule] | None = None,
    include_class_in_resource_mode: bool = True,
) -> SchedulePlan:
    rules = rules or []
    if not cases:
        return SchedulePlan(strategy=strategy, workers=workers)

    classes = {c.class_key for c in cases}
    if strategy == ScheduleStrategy.CASE:
        groups = [
            ScheduleGroup(
                name=_group_name(strategy, c.nodeid),
                case_nodeids=[c.nodeid],
                reason="case-parallel",
            )
            for c in cases
        ]
        notes = ["xdist --dist=load: any selected case may run in parallel."]
        parallelism = min(workers, len(cases))
    elif strategy == ScheduleStrategy.CLASS:
        by_key: dict[str, list[str]] = defaultdict(list)
        for c in cases:
            by_key[c.class_key].append(c.nodeid)
        groups = [
            ScheduleGroup(
                name=_group_name(strategy, k), case_nodeids=sorted(v), reason="same class serial"
            )
            for k, v in sorted(by_key.items())
        ]
        notes = ["xdist --dist=loadscope: same module+class always lands on one worker."]
        parallelism = min(workers, len(groups))
    elif strategy == ScheduleStrategy.FILE:
        by_file: dict[str, list[str]] = defaultdict(list)
        for c in cases:
            by_file[c.file].append(c.nodeid)
        groups = [
            ScheduleGroup(
                name=_group_name(strategy, k), case_nodeids=sorted(f), reason="same file serial"
            )
            for k, f in sorted(by_file.items())
        ]
        notes = ["xdist --dist=loadfile: same file always lands on one worker."]
        parallelism = min(workers, len(groups))
    else:  # RESOURCE
        groups, parallelism, notes = _plan_resource(
            cases, workers, rules, include_class_in_resource_mode
        )

    return SchedulePlan(
        strategy=strategy,
        groups=groups,
        workers=workers,
        selected_count=len(cases),
        class_count=len(classes),
        resource_groups=[g.resources for g in groups if g.resources],
        max_parallelism=parallelism,
        notes=notes,
    )


def _plan_resource(
    cases: list[TestCase],
    workers: int,
    rules: list[ResourceRule],
    include_class: bool,
) -> tuple[list[ScheduleGroup], int, list[str]]:
    nodeids = [c.nodeid for c in cases]
    uf = _UnionFind(nodeids)
    by_case_res: dict[str, list[str]] = {}
    # resource id -> first nodeid seen, to union subsequent sharers
    first_owner: dict[str, str] = {}

    for c in cases:
        res = resolve_resources(c, rules)
        if include_class:
            res = [*res, f"class:{c.class_key}"]
        by_case_res[c.nodeid] = res
        for r in res:
            if r in first_owner:
                uf.union(first_owner[r], c.nodeid)
            else:
                first_owner[r] = c.nodeid

    comp: dict[str, list[str]] = defaultdict(list)
    for nid in nodeids:
        comp[uf.find(nid)].append(nid)

    groups: list[ScheduleGroup] = []
    for idx, (_root, members) in enumerate(sorted(comp.items()), start=1):
        members = sorted(members)
        resources = sorted(
            {r for nid in members for r in by_case_res[nid] if not r.startswith("class:")}
        )
        reason = "shared resource" if resources else "class-isolated"
        groups.append(
            ScheduleGroup(
                name=f"resource:grp{idx:03d}",
                case_nodeids=members,
                reason=reason,
                resources=resources,
            )
        )
    notes = [
        "Conflict graph (union-find) merges any cases sharing a resource, transitively.",
        "Injected as @pytest.mark.xdist_group and executed with xdist --dist=loadgroup.",
    ]
    if include_class:
        notes.append("Each class is treated as an implicit resource (same class stays serial).")
    parallelism = min(workers, len(groups)) if groups else 0
    return groups, parallelism, notes


def measure_group_overlap(plan: SchedulePlan, case_worker: dict[str, str]) -> list[str]:
    """Return violations where two cases of one group ran on different workers.

    Used by tests to prove mutual exclusion actually held at runtime.
    """
    violations: list[str] = []
    for group in plan.groups:
        workers = {case_worker[n] for n in group.case_nodeids if n in case_worker}
        if len(workers) > 1:
            violations.append(f"{group.name} ran on workers {sorted(workers)}")
    return violations


def xdist_args(strategy: ScheduleStrategy, workers: int) -> list[str]:
    if workers <= 1:
        return []
    dist = {
        ScheduleStrategy.CASE: "load",
        ScheduleStrategy.CLASS: "loadscope",
        ScheduleStrategy.FILE: "loadfile",
        ScheduleStrategy.RESOURCE: "loadgroup",
    }[strategy]
    return ["-n", str(workers), "--dist", dist]


def group_map(plan: SchedulePlan) -> dict[str, str]:
    """nodeid -> xdist_group name, consumed by the runner plugin."""
    out: dict[str, str] = {}
    for g in plan.groups:
        for nid in g.case_nodeids:
            out[nid] = g.name
    return out
