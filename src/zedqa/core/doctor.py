"""`zedqa doctor`: verify the toolchain before running anything."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from zedqa.core.interpreter import check_import, find_allure, resolve_interpreter
from zedqa.core.models import DoctorCheck
from zedqa.reporting import allure as allure_mod


def _wsl() -> bool:
    return allure_mod.is_wsl()


def _playwright_browsers() -> tuple[bool, str]:
    roots = []
    if env := os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        roots.append(Path(env))
    roots.append(Path.home() / ".cache" / "ms-playwright")
    roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    roots.append(Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright")
    found = [r for r in roots if r.exists() and any(r.glob("chromium-*"))]
    if found:
        names = sorted(p.name for p in found[0].glob("chromium*"))
        return True, f"{found[0]} ({', '.join(names[:4])})"
    return False, "no chromium-* directory in playwright cache"


def run_doctor(root: Path, interpreter: str | None = None) -> list[DoctorCheck]:
    root = Path(root).resolve()
    checks: list[DoctorCheck] = []

    os_name = f"{platform.system()} {platform.release()}"
    if _wsl():
        os_name += " (WSL)"
    checks.append(DoctorCheck(name="OS", ok=True, detail=os_name))

    py = resolve_interpreter(root, interpreter)
    checks.append(DoctorCheck(name="Project interpreter", ok=True, detail=py))

    try:
        major, minor = sys.version_info[:2]
        version = ".".join(platform.python_version_tuple())
        checks.append(
            DoctorCheck(
                name="zedqa Python",
                ok=(major, minor) >= (3, 12),
                detail=version,
                required=True,
                hint="Python 3.12+ required (3.13 supported)",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(DoctorCheck(name="zedqa Python", ok=False, detail=str(exc), required=True))

    for label, module, required in (
        ("pytest", "pytest", True),
        ("pytest-xdist", "xdist", False),
        ("pytest-rerunfailures", "pytest_rerunfailures", False),
        ("pytest-timeout", "pytest_timeout", False),
        ("allure-pytest", "allure_pytest", False),
        ("pytest-playwright", "pytest_playwright", False),
        ("playwright", "playwright", False),
    ):
        ok, detail = check_import(py, module)
        checks.append(
            DoctorCheck(
                name=label,
                ok=ok,
                detail=detail if ok else "not importable in project interpreter",
                required=required,
                hint=None if ok else f"install `{label}` in the project environment",
            )
        )

    ok, detail = _playwright_browsers()
    checks.append(
        DoctorCheck(
            name="Playwright browsers",
            ok=ok,
            detail=detail,
            hint=None if ok else "run `playwright install chromium`",
        )
    )

    allure = find_allure()
    allure_ver = allure_mod.allure_version(allure) if allure else None
    checks.append(
        DoctorCheck(
            name="Allure CLI",
            ok=bool(allure),
            detail=f"{allure} (v{allure_ver})" if allure else "not found",
            hint=None if allure else "install Allure CLI or set ZEDQA_ALLURE",
        )
    )

    zed_dir = root / ".zed"
    checks.append(
        DoctorCheck(
            name="Zed integration",
            ok=zed_dir.exists(),
            detail=str(zed_dir) if zed_dir.exists() else "no .zed/ directory (run `zedqa init`)",
        )
    )
    cfg = root / "zedqa.toml"
    checks.append(
        DoctorCheck(
            name="zedqa.toml",
            ok=cfg.exists(),
            detail=str(cfg) if cfg.exists() else "no config (defaults will be used)",
        )
    )

    checks.append(DoctorCheck(name="Working directory", ok=True, detail=str(root)))
    return checks


def doctor_ok(checks: list[DoctorCheck]) -> bool:
    return all(c.ok for c in checks if c.required)
