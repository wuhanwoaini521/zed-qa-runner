"""Zed Tasks and Debug configuration templates plus a safe merge installer.

Existing `.zed/tasks.json` and `.zed/debug.json` are never overwritten: zedqa
entries are merged by label, existing labels win, and the previous file is backed
up once so `uninstall` can restore it.
"""

from __future__ import annotations

import json
from pathlib import Path

from zedqa.debugging.zed import config_for_nodeid, debug_configs, find_venv_python

TASK_LABELS_PREFIX = "zedqa:"
BACKUP_SUFFIX = ".zedqa-backup"


def default_tasks() -> list[dict]:
    return [
        {
            "label": "zedqa: Test Explorer (TUI)",
            "command": "zedqa ui",
            "cwd": "$ZED_WORKTREE_ROOT",
            "use_new_terminal": True,
            "allow_concurrent_runs": False,
            "reveal": "always",
        },
        {
            "label": "zedqa: Run current test file",
            "command": "zedqa run --file $ZED_RELATIVE_FILE",
            "cwd": "$ZED_WORKTREE_ROOT",
            "reveal": "always",
        },
        {
            "label": "zedqa: Run current test symbol",
            "command": "zedqa run --nodeid $ZED_RELATIVE_FILE::$ZED_SYMBOL",
            "cwd": "$ZED_WORKTREE_ROOT",
            "reveal": "always",
        },
        {
            "label": "zedqa: Rerun failed (latest run)",
            "command": "zedqa rerun --latest",
            "cwd": "$ZED_WORKTREE_ROOT",
            "reveal": "always",
        },
        {
            "label": "zedqa: Open Allure report (latest run)",
            "command": "zedqa report --latest --open",
            "cwd": "$ZED_WORKTREE_ROOT",
            "reveal": "always",
        },
        {
            "label": "zedqa: Discover tests",
            "command": "zedqa discover",
            "cwd": "$ZED_WORKTREE_ROOT",
            "reveal": "always",
        },
        {
            "label": "zedqa: Doctor",
            "command": "zedqa doctor",
            "cwd": "$ZED_WORKTREE_ROOT",
            "reveal": "always",
        },
    ]


def _read_json_array(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return data


def merge_by_label(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Keep existing entries; append incoming ones whose label is new."""
    seen = {e.get("label") for e in existing}
    merged = list(existing)
    for item in incoming:
        if item.get("label") not in seen:
            merged.append(item)
            seen.add(item.get("label"))
    return merged


def _backup_once(path: Path) -> None:
    if path.exists():
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def install_debug(root: Path, interpreter: str | None = None) -> Path:
    zed = root / ".zed"
    zed.mkdir(parents=True, exist_ok=True)
    path = zed / "debug.json"
    _backup_once(path)
    existing = _read_json_array(path)
    venv = find_venv_python(root)
    incoming = debug_configs(interpreter, venv)
    merged = merge_by_label(existing, incoming)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return path


def install_tasks(root: Path) -> Path:
    zed = root / ".zed"
    zed.mkdir(parents=True, exist_ok=True)
    path = zed / "tasks.json"
    _backup_once(path)
    existing = _read_json_array(path)
    with_comments_removed = [e for e in existing if not str(e.get("label", "")).startswith("//")]
    merged = merge_by_label(with_comments_removed, default_tasks())
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return path


def add_debug_for_nodeid(root: Path, nodeid: str) -> Path:
    zed = root / ".zed"
    zed.mkdir(parents=True, exist_ok=True)
    path = zed / "debug.json"
    existing = _read_json_array(path)
    entry = config_for_nodeid(nodeid, find_venv_python(root))
    # Replace an older entry for the same test, keep everything else.
    merged = [e for e in existing if e.get("label") != entry["label"]]
    merged.append(entry)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return path


def uninstall(root: Path) -> list[str]:
    """Remove zedqa-managed entries and restore backups. Returns changed files."""
    changed: list[str] = []
    zed = root / ".zed"
    for name, keep_label in (
        ("tasks.json", TASK_LABELS_PREFIX),
        ("debug.json", TASK_LABELS_PREFIX),
    ):
        path = zed / name
        if not path.exists():
            continue
        _backup_once(path)
        try:
            entries = _read_json_array(path)
        except ValueError:
            continue
        remaining = [e for e in entries if not str(e.get("label", "")).startswith(keep_label)]
        path.write_text(json.dumps(remaining, indent=2) + "\n", encoding="utf-8")
        changed.append(str(path))
    return changed
