"""Persistence of run manifests under .qa-runs/<run-id>/run.json."""

from __future__ import annotations

from pathlib import Path

from zedqa.core.models import RunManifest, RunSummary
from zedqa.core.paths import runs_dir


def manifest_path(root: Path, run_id: str, configured: str = ".qa-runs") -> Path:
    return runs_dir(root, configured) / run_id / "run.json"


def save_manifest(root: Path, manifest: RunManifest, configured: str = ".qa-runs") -> Path:
    path = manifest_path(root, manifest.run_id, configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest.run_dir = str(path.parent)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_manifest(root: Path, run_id: str, configured: str = ".qa-runs") -> RunManifest:
    path = manifest_path(root, run_id, configured)
    if not path.exists():
        raise FileNotFoundError(f"run not found: {run_id} ({path})")
    return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))


def list_runs(root: Path, configured: str = ".qa-runs") -> list[RunSummary]:
    base = runs_dir(root, configured)
    summaries: list[RunSummary] = []
    if not base.exists():
        return summaries
    for path in sorted(base.iterdir()):
        mpath = path / "run.json"
        if not path.is_dir() or not mpath.exists():
            continue
        try:
            m = RunManifest.model_validate_json(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summaries.append(
            RunSummary(
                run_id=m.run_id,
                state=m.state,
                created_at=m.created_at,
                total=m.counts.total,
                passed=m.counts.passed,
                failed=m.counts.failed + m.counts.error,
                flaky=m.counts.flaky,
                duration=m.duration,
                run_dir=str(path),
                report_exists=bool(m.allure_report and Path(m.allure_report).exists()),
            )
        )
    return summaries


def latest_run_id(root: Path, configured: str = ".qa-runs") -> str | None:
    runs = list_runs(root, configured)
    return runs[-1].run_id if runs else None
