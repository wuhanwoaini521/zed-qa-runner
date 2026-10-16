"""Argument construction, Allure integration and debug-safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from zedqa.core.models import RunConfig, RunManifest, ScheduleStrategy
from zedqa.debugging.zed import DEBUG_ARGS, config_for_nodeid, debug_configs
from zedqa.reporting import allure as allure_mod
from zedqa.runner.executor import Capabilities, build_args


def _manifest(**overrides) -> RunManifest:
    cfg = RunConfig(
        project_root="/proj",
        interpreter="/venv/bin/python",
        workers=4,
        strategy=ScheduleStrategy.RESOURCE,
        reruns=2,
        timeout=30.0,
        markers=["smoke"],
        keyword="login",
        selection=["tests/a.py::TestA::test_x[with space]", "tests/b.py::test_y"],
        extra_args=["--tb=short"],
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return RunManifest(run_id="r1", project_root="/proj", config=cfg)


def test_build_args_is_argv_list_and_scopes_selection(tmp_path: Path) -> None:
    args = build_args(_manifest(), Capabilities({"timeout": True}), tmp_path)
    assert all(isinstance(a, str) for a in args)
    assert "/venv/bin/python" in args and "-m" in args and "pytest" in args
    # exact parameterized selection preserved as one element
    assert "tests/a.py::TestA::test_x[with space]" in args
    assert "tests/b.py::test_y" in args
    # xdist, reruns, timeout, markers, keyword, reporting
    assert ["-n", "4", "--dist", "loadgroup"] == args[args.index("-n") : args.index("-n") + 4]
    assert "--reruns" in args and "2" in args
    assert "--timeout" in args
    assert "-m" in args and "smoke" in args
    assert "-k" in args and "login" in args
    assert "--alluredir" in args and "--junitxml" in args
    assert args[-2:] == ["--tb=short"] or args[-1] == "--tb=short"


def test_build_args_playwright_flags(tmp_path: Path) -> None:
    m = _manifest(strategy=ScheduleStrategy.CLASS, workers=2, browser="chromium", headless=False)
    m.config.extra_args = []
    args = build_args(m, Capabilities({"playwright": True}), tmp_path, uses_playwright=True)
    assert "--browser" in args and "chromium" in args
    assert "--headed" in args
    assert "--tracing" in args and "retain-on-failure" in args
    # no playwright flags when the selection is not playwright
    args2 = build_args(m, Capabilities({"playwright": True}), tmp_path, uses_playwright=False)
    assert "--browser" not in args2


def test_debug_configs_disable_xdist_and_reruns() -> None:
    for cfg in debug_configs(venv="$ZED_WORKTREE_ROOT/.venv/bin/python"):
        assert cfg["adapter"] == "Debugpy"
        assert cfg["module"] == "pytest"
        assert "-n0" in cfg["args"]
        assert "no:rerunfailures" in cfg["args"]
    assert "-n0" in DEBUG_ARGS
    single = config_for_nodeid("tests/a.py::TestA::test_x")
    assert single["args"][0] == "tests/a.py::TestA::test_x"


def test_allure_path_conversion_returns_string() -> None:
    assert isinstance(allure_mod.to_cli_path(Path(".")), str)


def test_allure_generate_requires_results(tmp_path: Path) -> None:
    ok, detail = allure_mod.generate_report(tmp_path / "missing", tmp_path / "out")
    assert not ok
    assert "no allure results" in detail or "not found" in detail


@pytest.mark.skipif(allure_mod.find_allure() is None, reason="Allure CLI not installed")
def test_allure_version_detected() -> None:
    version = allure_mod.allure_version()
    assert version and any(ch.isdigit() for ch in version)
