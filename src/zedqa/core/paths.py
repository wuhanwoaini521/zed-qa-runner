"""Run artifact directory layout: .qa-runs/<run-id>/."""

from __future__ import annotations

from pathlib import Path


def runs_dir(root: Path, configured: str = ".qa-runs") -> Path:
    p = Path(configured)
    return p if p.is_absolute() else root / p


def run_dir(root: Path, run_id: str, configured: str = ".qa-runs") -> Path:
    return runs_dir(root, configured) / run_id


def ensure_run_dirs(root: Path, run_id: str, configured: str = ".qa-runs") -> dict[str, Path]:
    base = run_dir(root, run_id, configured)
    dirs = {
        "base": base,
        "allure_results": base / "allure-results",
        "allure_report": base / "allure-report",
        "screenshots": base / "screenshots",
        "traces": base / "traces",
        "videos": base / "videos",
        "logs": base / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def find_run_dir(root: Path, run_id: str, configured: str = ".qa-runs") -> Path | None:
    d = run_dir(root, run_id, configured)
    return d if d.exists() else None


def latest_run(root: Path, configured: str = ".qa-runs") -> str | None:
    base = runs_dir(root, configured)
    if not base.exists():
        return None
    runs = [p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
    return sorted(runs)[-1] if runs else None
