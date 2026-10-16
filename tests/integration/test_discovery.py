"""Discovery integration tests against real pytest collection."""

from __future__ import annotations

from pathlib import Path

from zedqa.core.service import ProjectService


def test_demo_discovery_matches_pytest(demo_discovery) -> None:
    nodeids = {c.nodeid for c in demo_discovery.cases}
    assert len(nodeids) == 16
    # parametrized cases are distinct items
    assert "tests/test_login.py::TestLogin::test_login_param[alice]" in nodeids
    assert "tests/test_login.py::TestLogin::test_login_param[bob]" in nodeids
    # browser parametrization from pytest-playwright
    assert "tests/test_checkout.py::TestCheckout::test_open_page[chromium]" in nodeids
    # classes and markers
    assert {c.cls for c in demo_discovery.cases if c.cls} == {
        "TestLogin",
        "TestCart",
        "TestSearch",
        "TestCheckout",
    }
    smoke = next(c for c in demo_discovery.cases if c.name == "test_failure")
    assert "smoke" in smoke.markers
    assert demo_discovery.errors == []


def _write_project(root: Path, body: str, name: str = "test_x.py") -> None:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (root / "tests" / name).write_text(body, encoding="utf-8")


def test_collection_error_is_surfaced_not_ignored(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        "import definitely_not_a_real_module\n\ndef test_ok():\n    assert True\n",
        name="test_broken.py",
    )
    result = ProjectService(tmp_path).discover()
    assert result.errors, "collection errors must be reported"
    assert "definitely_not_a_real_module" in result.errors[0].message
    # a sibling healthy file still collects
    _write_project(
        tmp_path,
        "def test_good():\n    assert True\n",
        name="test_good.py",
    )
    result = ProjectService(tmp_path).discover()
    assert any(c.name == "test_good" for c in result.cases)
    assert result.errors


def test_discovery_does_not_execute_tests(tmp_path: Path) -> None:
    sentinel = tmp_path / "EXECUTED"
    _write_project(
        tmp_path,
        "from pathlib import Path\n"
        f"SENTINEL = Path(r'{sentinel}')\n"
        "def test_side_effect():\n"
        "    SENTINEL.write_text('ran')\n"
        "    assert True\n",
    )
    result = ProjectService(tmp_path).discover()
    assert any(c.name == "test_side_effect" for c in result.cases)
    assert not sentinel.exists(), "collection must never execute test bodies"


def test_discovery_respects_marker_and_keyword(demo_service) -> None:
    result = demo_service.discover(markers=["smoke"])
    assert result.cases
    assert all("smoke" in c.markers for c in result.cases)
    keyword = demo_service.discover(keyword="test_flaky")
    assert [c.name for c in keyword.cases] == ["test_flaky"]
