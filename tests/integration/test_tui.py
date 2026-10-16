"""TUI tests driven by Textual's headless pilot (no real terminal needed)."""

from __future__ import annotations

import asyncio
import time

from textual.widgets import DataTable, Input, RichLog

from zedqa.tui.app import ZedqaTUI


async def _wait_until(pilot, predicate, timeout: float = 90.0) -> None:  # noqa: ANN001
    start = time.time()
    while not predicate():
        if time.time() - start > timeout:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.1)


def test_tui_discovery_selection_filter_and_run(demo_root) -> None:
    async def scenario() -> None:
        app = ZedqaTUI(demo_root)
        async with app.run_test() as pilot:
            await _wait_until(pilot, lambda: app.discovery is not None)
            table = app.query_one("#tests", DataTable)
            assert len(table.rows) == 16

            # select all visible, then clear
            app.action_select_all()
            await pilot.pause()
            assert len(app.selected) == 16
            app.action_select_none()
            await pilot.pause()
            assert app.selected == set()

            # filter narrows the visible rows
            search = app.query_one("#search", Input)
            search.value = "test_flaky"
            await pilot.pause(0.3)
            assert len(table.rows) == 1

            search.value = ""
            await pilot.pause(0.3)
            assert len(table.rows) == 16

            # invert selection twice returns to empty
            app.action_invert()
            await pilot.pause()
            assert len(app.selected) == 16

            # run a single fast passing case through the TUI
            app.selected = {"tests/test_login.py::TestLogin::test_success"}
            app.action_run()
            await _wait_until(pilot, lambda: not app.running and app.last_manifest is not None)
            manifest = app.last_manifest
            assert manifest is not None
            assert manifest.counts.passed == 1
            log = app.query_one("#log", RichLog)
            assert log is not None

    asyncio.run(scenario())


def test_tui_strategy_and_worker_controls(demo_root) -> None:
    async def scenario() -> None:
        app = ZedqaTUI(demo_root)
        async with app.run_test() as pilot:
            await _wait_until(pilot, lambda: app.discovery is not None)
            start = app.strategy
            app.action_cycle_strategy()
            await pilot.pause()
            assert app.strategy != start
            before = app.worker_count
            app.action_more_workers()
            await pilot.pause()
            assert app.worker_count == before + 1
            app.action_fewer_workers()
            await pilot.pause()
            assert app.worker_count == before
            headless = app.headless
            app.action_toggle_headless()
            await pilot.pause()
            assert app.headless is not headless

    asyncio.run(scenario())
