"""CLI tests via Typer's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from zedqa.cli.main import app

runner = CliRunner()


def _simple_project(tmp: Path) -> Path:
    (tmp / "tests").mkdir(parents=True)
    (tmp / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (tmp / "tests" / "test_basic.py").write_text(
        "class TestBasic:\n"
        "    def test_ok(self):\n        assert True\n"
        "    def test_also_ok(self):\n        assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    return tmp


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "zedqa" in result.output.lower()


def test_doctor_on_demo(demo_root: Path) -> None:
    result = runner.invoke(app, ["doctor", "-C", str(demo_root)])
    assert "pytest" in result.output
    assert result.exit_code == 0


def test_discover_json(demo_root: Path) -> None:
    result = runner.invoke(app, ["discover", "-C", str(demo_root), "--json"])
    assert result.exit_code == 0
    # Rich may wrap JSON; strip whitespace and find the object.
    start = result.output.index("{")
    payload = json.loads(result.output[start:])
    assert len(payload["cases"]) == 16


def test_plan_all(demo_root: Path) -> None:
    result = runner.invoke(app, ["plan", "-C", str(demo_root), "--all"])
    assert result.exit_code == 0
    assert "Schedule preview" in result.output
    assert "groups" in result.output


def test_run_single_passing_case(demo_root: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "-C",
            str(demo_root),
            "--nodeid",
            "tests/test_login.py::TestLogin::test_success",
            "--workers",
            "1",
        ],
    )
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_run_unknown_nodeid_aborts(demo_root: Path) -> None:
    result = runner.invoke(
        app, ["run", "-C", str(demo_root), "--nodeid", "does/not/exist.py::test_x"]
    )
    assert result.exit_code == 2
    assert "unknown nodeids" in result.output


def test_runs_and_failures_and_report(tmp_path: Path) -> None:
    _simple_project(tmp_path)
    # make a failing run so `failures` has content
    (tmp_path / "tests" / "test_fail.py").write_text(
        "def test_bad():\n    assert False, 'intentional'\n", encoding="utf-8"
    )
    run = runner.invoke(app, ["run", "-C", str(tmp_path), "--all", "--workers", "1"])
    assert run.exit_code == 1

    runs = runner.invoke(app, ["runs", "-C", str(tmp_path), "--json"])
    assert runs.exit_code == 0
    assert "run_id" in runs.output

    failures = runner.invoke(app, ["failures", "-C", str(tmp_path), "--latest"])
    assert failures.exit_code == 0
    assert "test_bad" in failures.output

    report = runner.invoke(app, ["report", "-C", str(tmp_path), "--latest"])
    assert report.exit_code == 0
    assert "allure-report" in report.output


def test_rerun_failed_only(tmp_path: Path) -> None:
    _simple_project(tmp_path)
    (tmp_path / "tests" / "test_fail.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    runner.invoke(app, ["run", "-C", str(tmp_path), "--all", "--workers", "1"])
    rerun = runner.invoke(app, ["rerun", "-C", str(tmp_path), "--latest"])
    assert rerun.exit_code == 1
    # only the failed case was executed (3 total results would mean --all leaked)
    assert "test_bad" in rerun.output


def test_suites_lifecycle(tmp_path: Path) -> None:
    _simple_project(tmp_path)
    runner.invoke(app, ["init", "-C", str(tmp_path)])
    created = runner.invoke(
        app,
        [
            "suites",
            "create",
            "smoke",
            "-C",
            str(tmp_path),
            "--nodeid",
            "tests/test_basic.py::TestBasic::test_ok",
        ],
    )
    assert created.exit_code == 0

    listed = runner.invoke(app, ["suites", "list", "-C", str(tmp_path)])
    assert "smoke" in listed.output

    shown = runner.invoke(app, ["suites", "show", "smoke", "-C", str(tmp_path)])
    assert "test_ok" in shown.output

    ran = runner.invoke(app, ["suites", "run", "smoke", "-C", str(tmp_path), "--workers", "1"])
    assert ran.exit_code == 0, ran.output

    deleted = runner.invoke(app, ["suites", "delete", "smoke", "-C", str(tmp_path)])
    assert "deleted" in deleted.output


def test_suite_reports_missing_nodeid(tmp_path: Path) -> None:
    _simple_project(tmp_path)
    runner.invoke(app, ["init", "-C", str(tmp_path)])
    runner.invoke(
        app,
        [
            "suites",
            "create",
            "stale",
            "-C",
            str(tmp_path),
            "--nodeid",
            "tests/test_basic.py::TestBasic::test_ok",
        ],
    )
    # rename the test so the saved nodeid no longer exists
    (tmp_path / "tests" / "test_basic.py").write_text(
        "class TestBasic:\n    def test_renamed(self):\n        assert True\n",
        encoding="utf-8",
    )
    shown = runner.invoke(app, ["suites", "show", "stale", "-C", str(tmp_path)])
    assert "missing" in shown.output


def test_init_merges_and_uninstalls(tmp_path: Path) -> None:
    _simple_project(tmp_path)
    zed = tmp_path / ".zed"
    zed.mkdir()
    (zed / "tasks.json").write_text(json.dumps([{"label": "keepme", "command": "true"}]))
    init = runner.invoke(app, ["init", "-C", str(tmp_path)])
    assert init.exit_code == 0
    tasks = json.loads((zed / "tasks.json").read_text())
    labels = [t["label"] for t in tasks]
    assert "keepme" in labels and any(lbl.startswith("zedqa:") for lbl in labels)
    assert (zed / "debug.json").exists()
    assert (tmp_path / "zedqa.toml").exists()

    uninstall = runner.invoke(app, ["init", "-C", str(tmp_path), "--uninstall"])
    assert uninstall.exit_code == 0
    remaining = json.loads((zed / "tasks.json").read_text())
    assert [t["label"] for t in remaining] == ["keepme"]


def test_debug_writes_config(tmp_path: Path) -> None:
    _simple_project(tmp_path)
    result = runner.invoke(
        app,
        ["debug", "-C", str(tmp_path), "--nodeid", "tests/test_basic.py::TestBasic::test_ok"],
    )
    assert result.exit_code == 0
    data = json.loads((tmp_path / ".zed" / "debug.json").read_text())
    assert any("test_ok" in d["label"] for d in data)
