"""Shared fixtures for the zedqa test suite.

Real pytest runs are expensive, so successful runs are cached per config key for
the whole session. No test fabricates results: every manifest comes from an actual
subprocess run of the demo project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zedqa.core.models import DiscoveryResult, RunConfig, RunManifest, ScheduleStrategy
from zedqa.core.service import ProjectService

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = REPO_ROOT / "examples" / "demo_pytest_project"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def demo_root() -> Path:
    return DEMO_ROOT


@pytest.fixture(scope="session")
def demo_service() -> ProjectService:
    return ProjectService(DEMO_ROOT)


@pytest.fixture(scope="session")
def demo_discovery(demo_service: ProjectService) -> DiscoveryResult:
    result = demo_service.discover()
    assert result.cases, "demo discovery returned no cases"
    return result


@pytest.fixture(scope="session")
def demo_run(demo_service: ProjectService) -> object:
    """Factory that runs the demo project once per (strategy, workers, reruns, selection)."""
    cache: dict[tuple, RunManifest] = {}

    def _run(
        selection: list[str] | None = None,
        strategy: ScheduleStrategy = ScheduleStrategy.RESOURCE,
        workers: int = 4,
        reruns: int = 1,
        cases=None,  # noqa: ANN001
    ) -> RunManifest:
        if cases is None:
            discovery = demo_service.discover()
            cases = [c for c in discovery.cases if selection is None or c.nodeid in selection]
        key = (strategy.value, workers, reruns, tuple(sorted(c.nodeid for c in cases)))
        if key not in cache:
            cfg = RunConfig(
                project_root=str(demo_service.root),
                workers=workers,
                strategy=strategy,
                reruns=reruns,
            )
            cache[key] = demo_service.run(cases, cfg)
        return cache[key]

    return _run
