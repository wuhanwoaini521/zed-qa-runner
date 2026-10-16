"""Machine-local cross-process resource locks.

Used to stop two independent zedqa runs (or two xdist workers) from touching the
same account/region at once. Implemented with atomic O_EXCL lock files plus PID
and age based stale detection, so a crashed process cannot wedge a resource forever.

shortcut: single-machine only (shared filesystem). A network/Redis backend is a
future adapter, not implemented here.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from zedqa.core.models import ResourceRule
from zedqa.scheduler.planner import resolve_resources


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - exercised only on Windows
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)[:120]


class LockTimeout(RuntimeError):
    pass


class FileLock:
    def __init__(self, lock_dir: Path, resource: str, stale_after: float = 600.0) -> None:
        self.resource = resource
        self.path = Path(lock_dir) / f"{_safe(resource)}.lock"
        self.stale_after = stale_after
        self._owned = False
        self._fd: int | None = None

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _stale(self) -> bool:
        info = self._read()
        age = time.time() - float(info.get("time", 0))
        if age > self.stale_after:
            return True
        pid = int(info.get("pid", 0))
        return not _pid_alive(pid)

    def acquire(self, timeout: float = 30.0, poll: float = 0.05) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._stale():
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"resource '{self.resource}' busy; held by pid {self._read().get('pid')}"
                    ) from None
                time.sleep(poll)
                continue
            os.write(
                fd,
                json.dumps(
                    {"pid": os.getpid(), "time": time.time(), "resource": self.resource}
                ).encode(),
            )
            os.close(fd)
            self._owned = True
            return

    def release(self) -> None:
        if not self._owned:
            return
        info = self._read()
        if int(info.get("pid", -1)) == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
        self._owned = False

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class ResourceLockManager:
    """Acquires a set of resource locks in a canonical order to avoid deadlock."""

    def __init__(self, lock_dir: Path, stale_after: float = 600.0) -> None:
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.stale_after = stale_after
        self._held: dict[str, FileLock] = {}

    def acquire_many(self, resources: list[str], timeout: float = 30.0) -> None:
        acquired_here: list[str] = []
        try:
            for res in sorted(set(resources)):
                if res in self._held:
                    continue
                lock = FileLock(self.lock_dir, res, self.stale_after)
                lock.acquire(timeout=timeout)
                self._held[res] = lock
                acquired_here.append(res)
        except Exception:
            # Roll back so a partial acquisition cannot leak a held resource.
            for res in acquired_here:
                held = self._held.pop(res, None)
                if held is not None:
                    held.release()
            raise

    def release_many(self, resources: list[str] | None = None) -> None:
        names = list(self._held) if resources is None else list(set(resources))
        for res in names:
            lock = self._held.pop(res, None)
            if lock:
                lock.release()

    def release_all(self) -> None:
        self.release_many()

    @contextmanager
    def hold(self, resources: list[str], timeout: float = 30.0) -> Iterator[None]:
        self.acquire_many(resources, timeout)
        try:
            yield
        finally:
            self.release_many(resources)


def resources_for_nodeid(
    nodeid: str, class_key_lookup: dict[str, str], rules: list[ResourceRule]
) -> list[str]:
    """Small helper for plugins that only have a nodeid."""
    from zedqa.core.models import TestCase

    cls = class_key_lookup.get(nodeid)
    case = TestCase(
        nodeid=nodeid,
        name=nodeid.split("::")[-1],
        file=nodeid.split("::")[0],
        cls=cls.split("::")[-1] if cls and "::" in cls else cls,
    )
    return resolve_resources(case, rules)
