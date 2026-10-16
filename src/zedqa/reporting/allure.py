"""Allure CLI integration.

Detects the installed Allure version and generates a report without mixing
Allure 2/3 arguments. On WSL the Allure CLI may be a Windows binary, so Unix
paths are converted with `wslpath`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from zedqa.core.interpreter import find_allure


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def to_cli_path(path: Path) -> str:
    """Convert a Unix path to a Windows path when the Allure CLI is a Windows exe."""
    p = str(path.resolve())
    if os.name == "nt":
        return p
    if is_wsl() and shutil.which("wslpath"):
        try:
            out = subprocess.run(["wslpath", "-w", p], capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return p


def _run_cli(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    # The Windows Allure shim can print GBK-encoded warnings, so never decode as strict UTF-8.
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def allure_version(exe: str | None = None) -> str | None:
    exe = exe or find_allure()
    if not exe:
        return None
    try:
        proc = _run_cli([exe, "--version"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout + proc.stderr).strip()
    for token in text.replace("\n", " ").split():
        if token[:1].isdigit() and "." in token:
            return token
    return text.splitlines()[-1] if text else None


def generate_report(
    results_dir: Path, report_dir: Path, exe: str | None = None, clean: bool = True
) -> tuple[bool, str]:
    """Return (ok, detail). Never merges test status with report status."""
    exe = exe or find_allure()
    if not exe:
        return False, "Allure CLI not found; install it or set ZEDQA_ALLURE"
    if not results_dir.exists() or not any(results_dir.iterdir()):
        return False, f"no allure results in {results_dir}"

    version = allure_version(exe) or "unknown"
    args = [exe, "generate", to_cli_path(results_dir), "-o", to_cli_path(report_dir)]
    if clean:
        args.append("--clean")
    try:
        proc = _run_cli(args, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"allure generate failed: {exc}"
    if proc.returncode != 0 and clean:
        # Some Allure builds reject --clean; retry without it.
        return generate_report(results_dir, report_dir, exe=exe, clean=False)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        return False, f"allure {version} generate failed: {' | '.join(tail)}"
    return True, f"allure {version} report generated at {report_dir}"


def open_report(report_dir: Path, exe: str | None = None) -> tuple[bool, str]:
    exe = exe or find_allure()
    if not exe:
        return False, "Allure CLI not found"
    if not report_dir.exists():
        return False, f"report dir does not exist: {report_dir}"
    cmd = [exe, "open", to_cli_path(report_dir)]
    try:
        # Detached: the server keeps running after zedqa exits.
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, f"could not open report: {exc}"
    return True, f"opening {report_dir}"
