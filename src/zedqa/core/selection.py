"""Resolve user selection (nodeid / file / class / marker / keyword) to cases."""

from __future__ import annotations

from zedqa.core.models import DiscoveryResult, TestCase


def select_cases(
    discovery: DiscoveryResult,
    nodeids: list[str] | None = None,
    files: list[str] | None = None,
    classes: list[str] | None = None,
    markers: list[str] | None = None,
    keyword: str | None = None,
    all_: bool = False,
) -> tuple[list[TestCase], list[str]]:
    """Return (selected cases, unknown nodeids). Unknown nodeids are surfaced, never ignored."""
    cases = list(discovery.cases)
    unknown: list[str] = []

    if nodeids:
        by_id = {c.nodeid: c for c in cases}
        picked: list[TestCase] = []
        for nid in nodeids:
            if nid in by_id:
                picked.append(by_id[nid])
            else:
                unknown.append(nid)
        cases = picked
    elif not (files or classes or markers or keyword or all_):
        return [], []

    if files:
        wanted = {f.replace("\\", "/").lstrip("./") for f in files}
        cases = [c for c in cases if c.file in wanted or c.file.endswith(tuple(wanted))]
    if classes:
        wanted = set(classes)
        cases = [c for c in cases if c.cls in wanted]
    if markers:
        cases = [c for c in cases if any(c.has_marker(m) for m in markers)]
    if keyword:
        cases = [c for c in cases if keyword in c.nodeid]

    # de-duplicate, preserve pytest order
    seen: set[str] = set()
    out: list[TestCase] = []
    for c in cases:
        if c.nodeid not in seen:
            seen.add(c.nodeid)
            out.append(c)
    return out, unknown
