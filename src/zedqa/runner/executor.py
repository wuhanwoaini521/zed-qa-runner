"""Subprocess execution of pytest with real-time structured event streaming.

Commands are always built as argv lists (never shell strings) so nodeids with
spaces, brackets or unicode work everywhere, including Windows.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from zedqa.core.interpreter import pytest_command
from zedqa.core.models import RunEvent, RunManifest


class Capabilities(dict):
    """Which optional pytest plugins are importable in the target environment."""

    @property
    def playwright(self) -> bool:
        return bool(self.get("playwright"))

    @property
    def timeout(self) -> bool:
        return bool(self.get("timeout"))


def build_args(
    manifest: RunManifest,
    caps: Capabilities,
    run_dir: Path,
    uses_playwright: bool = False,
) -> list[str]:
    from zedqa.scheduler.planner import xdist_args

    cfg = manifest.config
    args = [*pytest_command(manifest.config.interpreter or "python")]
    args += list(cfg.selection)

    args += xdist_args(cfg.strategy, cfg.workers)

    if cfg.markers:
        args += ["-m", " or ".join(cfg.markers)]
    if cfg.keyword:
        args += ["-k", cfg.keyword]
    if cfg.reruns > 0:
        args += ["--reruns", str(cfg.reruns), "--reruns-delay", "0"]
    if cfg.timeout and caps.timeout:
        args += ["--timeout", str(cfg.timeout)]
    if cfg.fail_fast:
        args.append("-x")

    # Plugins
    args += ["-p", "zedqa.runner.pytest_plugin"]
    args += ["-p", "zedqa.discovery.collect_plugin"]  # harmless during run; keeps env consistent

    # Reporting
    args += ["--alluredir", str(run_dir / "allure-results")]
    args += ["--junitxml", str(run_dir / "junit.xml")]
    args += ["-o", f"junit_suite_name=zedqa-{manifest.run_id}"]

    # Playwright: reuse pytest-playwright for browser selection and artifacts.
    if caps.playwright and uses_playwright:
        args += ["--browser", cfg.browser]
        if not cfg.headless:
            args += ["--headed"]
        args += [
            "--tracing",
            "retain-on-failure",
            "--video",
            "retain-on-failure",
            "--output",
            str(run_dir / "playwright-artifacts"),
        ]

    args += list(cfg.extra_args)
    return args


def detect_capabilities(interpreter: str) -> Capabilities:
    from zedqa.core.interpreter import check_import

    caps = Capabilities()
    for key, module in (
        ("playwright", "pytest_playwright"),
        ("timeout", "pytest_timeout"),
        ("allure", "allure_pytest"),
        ("xdist", "xdist"),
        ("rerun", "pytest_rerunfailures"),
    ):
        ok, _ = check_import(interpreter, module)
        caps[key] = ok
    return caps


def _tail_events(
    event_file: Path,
    stop: threading.Event,
    on_event: Callable[[RunEvent], None] | None,
) -> None:
    pos = 0
    buffer = ""
    while True:
        exists = event_file.exists()
        if exists:
            try:
                with event_file.open("r", encoding="utf-8") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
            except OSError:
                chunk = ""
            if chunk:
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if on_event:
                        on_event(RunEvent.from_raw(raw))
        if stop.is_set() and not chunk:
            # final drain
            if not buffer.strip():
                return
        time.sleep(0.08)


def stream_run(
    root: Path,
    interpreter: str,
    args: list[str],
    env: dict[str, str],
    run_dir: Path,
    cancel_event: threading.Event | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    on_output: Callable[[str], None] | None = None,
) -> int:
    """Run pytest, stream events, return exit code. Handles cancellation."""
    log_path = run_dir / "logs" / "pytest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    event_file = Path(env["ZEDQA_EVENT_FILE"])
    event_file.parent.mkdir(parents=True, exist_ok=True)
    event_file.write_text("", encoding="utf-8")  # truncate

    stop = threading.Event()
    tailer = threading.Thread(target=_tail_events, args=(event_file, stop, on_event), daemon=True)
    tailer.start()

    creationflags = 0
    if os.name == "nt":  # pragma: no cover
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    with log_path.open("w", encoding="utf-8") as logfh:
        proc = subprocess.Popen(
            args,
            cwd=str(root),
            env=env,
            stdout=logfh,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        cancelled = False
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                _terminate(proc)
                break
            time.sleep(0.1)
        exit_code = proc.wait()
        if cancelled:
            exit_code = 130

    # Let the tailer drain remaining lines.
    time.sleep(0.15)
    stop.set()
    tailer.join(timeout=3)

    if on_output and log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            on_output(line)
    return exit_code


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except OSError:
        pass


def write_json_map(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
