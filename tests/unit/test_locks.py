"""Cross-process resource lock tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from zedqa.resources.locks import FileLock, LockTimeout, ResourceLockManager


def test_acquire_and_release(tmp_path: Path) -> None:
    lock = FileLock(tmp_path, "account:A")
    lock.acquire(timeout=1)
    assert lock.path.exists()
    lock.release()
    assert not lock.path.exists()


def test_second_acquire_times_out_while_held(tmp_path: Path) -> None:
    a = FileLock(tmp_path, "account:A")
    b = FileLock(tmp_path, "account:A")
    a.acquire(timeout=1)
    started = time.monotonic()
    with pytest.raises(LockTimeout):
        b.acquire(timeout=0.3)
    assert time.monotonic() - started >= 0.3
    a.release()
    b.acquire(timeout=1)  # now free
    b.release()


def test_stale_lock_from_dead_pid_is_reclaimed(tmp_path: Path) -> None:
    lock = FileLock(tmp_path, "region:JP")
    lock.path.write_text(json.dumps({"pid": 999_999_999, "time": time.time()}), encoding="utf-8")
    lock.acquire(timeout=1)  # dead pid -> reclaimed
    assert lock.path.exists()
    lock.release()


def test_stale_lock_by_age_is_reclaimed(tmp_path: Path) -> None:
    lock = FileLock(tmp_path, "region:JP", stale_after=0.1)
    lock.path.write_text(
        json.dumps({"pid": os.getpid(), "time": time.time() - 100}), encoding="utf-8"
    )
    lock.acquire(timeout=1)
    assert lock.path.exists()
    lock.release()


def test_cross_process_exclusion(tmp_path: Path) -> None:
    """A real child process holds the lock; the parent cannot acquire it."""
    script = (
        "import sys, time; from zedqa.resources.locks import FileLock;"
        f"l=FileLock(r'{tmp_path}','account:X'); l.acquire(timeout=5); print('held', flush=True);"
        "time.sleep(2); l.release()"
    )
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        lock = FileLock(tmp_path, "account:X")
        with pytest.raises(LockTimeout):
            lock.acquire(timeout=0.4)
    finally:
        proc.wait(timeout=10)


def test_manager_acquires_sorted_and_releases(tmp_path: Path) -> None:
    mgr = ResourceLockManager(tmp_path)
    mgr.acquire_many(["region:JP", "account:A", "account:A"], timeout=1)
    assert (tmp_path / "account_A.lock").exists()
    assert (tmp_path / "region_JP.lock").exists()
    mgr.release_all()
    assert not list(tmp_path.glob("*.lock"))


def test_manager_rolls_back_on_failure(tmp_path: Path) -> None:
    blocker = FileLock(tmp_path, "account:B")
    blocker.acquire(timeout=1)
    mgr = ResourceLockManager(tmp_path)
    with pytest.raises(LockTimeout):
        # account:A acquired first (sorted), then account:B blocks; A must be released.
        mgr.acquire_many(["account:B", "account:A"], timeout=0.3)
    assert not (tmp_path / "account_A.lock").exists()
    blocker.release()
