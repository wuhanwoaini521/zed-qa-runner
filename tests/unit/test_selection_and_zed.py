"""Selection, config and Zed integration unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zedqa.core.config import load_config, save_config
from zedqa.core.models import DiscoveryResult, TestCase, ZedqaConfig
from zedqa.core.selection import select_cases
from zedqa.integrations import zed as zed_integration

DISCOVERY = DiscoveryResult(
    root="/proj",
    interpreter="python",
    cases=[
        TestCase(
            nodeid="tests/test_login.py::TestLogin::test_a",
            name="test_a",
            file="tests/test_login.py",
            cls="TestLogin",
            markers=["smoke"],
        ),
        TestCase(
            nodeid="tests/test_login.py::TestLogin::test_b",
            name="test_b",
            file="tests/test_login.py",
            cls="TestLogin",
            markers=["slow"],
        ),
        TestCase(
            nodeid="tests/test_cart.py::TestCart::test_c",
            name="test_c",
            file="tests/test_cart.py",
            cls="TestCart",
            markers=["smoke"],
        ),
        TestCase(
            nodeid="tests/test_search.py::test_param[alice]",
            name="test_param[alice]",
            file="tests/test_search.py",
            markers=[],
        ),
    ],
)


def test_select_by_nodeid_and_unknown() -> None:
    cases, unknown = select_cases(
        DISCOVERY, nodeids=["tests/test_cart.py::TestCart::test_c", "nope"]
    )
    assert [c.nodeid for c in cases] == ["tests/test_cart.py::TestCart::test_c"]
    assert unknown == ["nope"]


def test_select_by_file_class_marker_keyword() -> None:
    cases, _ = select_cases(DISCOVERY, files=["tests/test_login.py"])
    assert len(cases) == 2
    cases, _ = select_cases(DISCOVERY, classes=["TestCart"])
    assert len(cases) == 1
    cases, _ = select_cases(DISCOVERY, markers=["smoke"])
    assert {c.nodeid.split("::")[-1] for c in cases} == {"test_a", "test_c"}
    cases, _ = select_cases(DISCOVERY, keyword="alice")
    assert len(cases) == 1


def test_select_all_and_empty() -> None:
    cases, _ = select_cases(DISCOVERY, all_=True)
    assert len(cases) == 4
    cases, _ = select_cases(DISCOVERY)
    assert cases == []


def test_config_roundtrip(tmp_path: Path) -> None:
    cfg = ZedqaConfig()
    cfg.scheduling.default_workers = 7
    cfg.resources.rules  # noqa: B018
    save_config(tmp_path, cfg)
    loaded = load_config(tmp_path)
    assert loaded.scheduling.default_workers == 7
    # missing file -> defaults
    assert load_config(tmp_path / "nope").version == 1


def test_merge_does_not_overwrite_existing(tmp_path: Path) -> None:
    zed = tmp_path / ".zed"
    zed.mkdir()
    (zed / "tasks.json").write_text(json.dumps([{"label": "mine", "command": "echo hi"}]))
    zed_integration.install_tasks(tmp_path)
    data = json.loads((zed / "tasks.json").read_text())
    labels = [t["label"] for t in data]
    assert "mine" in labels
    assert any(lbl.startswith("zedqa:") for lbl in labels)
    # backup created once
    assert (zed / "tasks.json.zedqa-backup").exists()


def test_install_is_idempotent(tmp_path: Path) -> None:
    zed_integration.install_tasks(tmp_path)
    first = json.loads((tmp_path / ".zed" / "tasks.json").read_text())
    zed_integration.install_tasks(tmp_path)
    second = json.loads((tmp_path / ".zed" / "tasks.json").read_text())
    assert len(first) == len(second)


def test_uninstall_removes_only_zedqa_entries(tmp_path: Path) -> None:
    zed = tmp_path / ".zed"
    zed.mkdir()
    (zed / "tasks.json").write_text(json.dumps([{"label": "mine", "command": "x"}]))
    zed_integration.install_tasks(tmp_path)
    zed_integration.uninstall(tmp_path)
    data = json.loads((zed / "tasks.json").read_text())
    assert [t["label"] for t in data] == ["mine"]


def test_add_debug_for_nodeid_replaces_same_label(tmp_path: Path) -> None:
    zed_integration.add_debug_for_nodeid(tmp_path, "tests/test_login.py::TestLogin::test_a")
    zed_integration.add_debug_for_nodeid(tmp_path, "tests/test_login.py::TestLogin::test_a")
    data = json.loads((tmp_path / ".zed" / "debug.json").read_text())
    matching = [d for d in data if "test_a" in d["label"]]
    assert len(matching) == 1
    assert matching[0]["adapter"] == "Debugpy"
    assert matching[0]["args"][0] == "tests/test_login.py::TestLogin::test_a"


def test_invalid_json_is_reported(tmp_path: Path) -> None:
    zed = tmp_path / ".zed"
    zed.mkdir()
    (zed / "tasks.json").write_text("{not json")
    with pytest.raises(ValueError):
        zed_integration.install_tasks(tmp_path)
