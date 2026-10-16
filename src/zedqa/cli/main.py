"""zedqa command line interface.

The CLI and the TUI both go through ProjectService; there is no second execution path.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from zedqa import __version__
from zedqa.core import doctor as doctor_mod
from zedqa.core.config import config_path, default_toml, load_config
from zedqa.core.models import (
    RunConfig,
    RunEvent,
    RunManifest,
    ScheduleStrategy,
    Suite,
)
from zedqa.core.selection import select_cases
from zedqa.core.service import ProjectService
from zedqa.integrations import zed as zed_integration
from zedqa.reporting import allure as allure_mod

app = typer.Typer(
    name="zedqa",
    help="Zed QA Runner: pytest + Playwright explorer, scheduler, runner and Allure reports.",
    no_args_is_help=True,
    add_completion=False,
)
suites_app = typer.Typer(help="Manage saved test suites.", no_args_is_help=True)
app.add_typer(suites_app, name="suites")

console = Console()
err_console = Console(stderr=True)


def esc(text: object) -> str:
    """Escape user/pytest text so Rich does not swallow nodeid params like [alice]."""
    return escape(str(text))


ROOT_OPTION = typer.Option(Path("."), "--root", "-C", help="Project root (default: cwd).")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"zedqa {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """Zed QA Runner."""


def _service(root: Path) -> ProjectService:
    return ProjectService(root)


def _strategy(value: str | None) -> ScheduleStrategy | None:
    return ScheduleStrategy(value) if value else None


# --------------------------------------------------------------------------- init
@app.command()
def init(
    root: Path = ROOT_OPTION,
    force: bool = typer.Option(
        False, "--force", help="Overwrite zedqa.toml (Zed files are still merged)."
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Remove zedqa entries from .zed files."
    ),
    restore: bool = typer.Option(False, "--restore", help="Restore .zed files from backups."),
) -> None:
    """Install (or remove) the zedqa integration in an existing Python project."""
    root = root.resolve()
    if uninstall:
        changed = zed_integration.uninstall(root)
        console.print(f"[green]removed zedqa entries[/] from: {changed or 'nothing'}")
        return
    if restore:
        restored = []
        for name in ("tasks.json", "debug.json"):
            backup = root / ".zed" / (name + zed_integration.BACKUP_SUFFIX)
            if backup.exists():
                (root / ".zed" / name).write_text(
                    backup.read_text(encoding="utf-8"), encoding="utf-8"
                )
                restored.append(name)
        console.print(f"[green]restored:[/] {restored or 'no backups found'}")
        return

    cfg = config_path(root)
    if cfg.exists() and not force:
        console.print(f"[yellow]kept existing[/] {cfg}")
    else:
        cfg.write_text(default_toml(), encoding="utf-8")
        console.print(f"[green]wrote[/] {cfg}")

    tasks = zed_integration.install_tasks(root)
    debug = zed_integration.install_debug(root)
    console.print(f"[green]merged[/] {tasks}")
    console.print(f"[green]merged[/] {debug}")
    console.print(
        Panel(
            "Open Zed in this project, then run the task [bold]zedqa: Test Explorer (TUI)[/],\n"
            "or press Ctrl+Shift+P and search for 'task: spawn'.\n"
            "Uninstall with: [bold]zedqa init --uninstall[/]   Restore: [bold]zedqa init --restore[/]",
            title="Zed integration ready",
        )
    )


# -------------------------------------------------------------------------- doctor
@app.command()
def doctor(root: Path = ROOT_OPTION, interpreter: str = typer.Option(None, "--python")) -> None:
    """Check the toolchain (Python, pytest plugins, Playwright, Allure, Zed)."""
    checks = doctor_mod.run_doctor(root.resolve(), interpreter)
    table = Table(title="zedqa doctor", show_lines=False)
    table.add_column("check")
    table.add_column("ok", justify="center")
    table.add_column("detail")
    for c in checks:
        mark = "[green]OK[/]" if c.ok else ("[red]FAIL[/]" if c.required else "[yellow]warn[/]")
        if not c.ok and c.hint:
            c = c.model_copy(update={"detail": f"{c.detail} — {c.hint}"})
        table.add_row(c.name, mark, c.detail)
    console.print(table)
    if not doctor_mod.doctor_ok(checks):
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------- discover
@app.command()
def discover(
    root: Path = ROOT_OPTION,
    marker: list[str] = typer.Option(None, "--marker", "-m"),
    keyword: str = typer.Option(None, "--keyword", "-k"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Collect tests using real pytest collection."""
    svc = _service(root)
    result = svc.discover(markers=marker, keyword=keyword)
    if as_json:
        console.print_json(result.model_dump_json())
        return
    _print_discovery(result)


def _print_discovery(result) -> None:  # noqa: ANN001
    table = Table(title=f"Discovered {len(result.cases)} case(s) in {result.duration:.2f}s")
    table.add_column("file")
    table.add_column("class")
    table.add_column("case")
    table.add_column("markers")
    for c in result.cases:
        table.add_row(esc(c.file), esc(c.cls or ""), esc(c.name), esc(", ".join(c.markers)))
    console.print(table)
    for e in result.errors:
        err_console.print(f"[red]collection error[/] {e.file or ''}: {e.message[:400]}")


# --------------------------------------------------------------------------- plan
@app.command()
def plan(
    root: Path = ROOT_OPTION,
    nodeid: list[str] = typer.Option(None, "--nodeid"),
    file: list[str] = typer.Option(None, "--file"),
    cls: list[str] = typer.Option(None, "--class"),
    marker: list[str] = typer.Option(None, "--marker", "-m"),
    keyword: str = typer.Option(None, "--keyword", "-k"),
    all_: bool = typer.Option(False, "--all"),
    workers: int = typer.Option(2, "--workers", "-n"),
    strategy: str = typer.Option(None, "--strategy", help="case|class|file|resource"),
) -> None:
    """Show the schedule preview without running anything."""
    svc = _service(root)
    discovery = svc.discover()
    cases, unknown = select_cases(discovery, nodeid, file, cls, marker, keyword, all_)
    if unknown:
        err_console.print(f"[yellow]unknown nodeids:[/] {unknown}")
    if not cases:
        err_console.print("[red]no cases selected[/]")
        raise typer.Exit(code=2)
    cfg = RunConfig(
        project_root=str(svc.root),
        workers=workers,
        strategy=_strategy(strategy) or svc.config.scheduling.default_strategy,
    )
    preview = svc.plan(cases, cfg)
    console.print(Panel("\n".join(preview.summary_lines()), title="Schedule preview (planned)"))
    console.print("[dim]This is the plan; the actual worker assignment is shown after the run.[/]")


# ---------------------------------------------------------------------------- run
@app.command()
def run(
    root: Path = ROOT_OPTION,
    nodeid: list[str] = typer.Option(None, "--nodeid", help="Exact pytest nodeid (repeatable)."),
    file: list[str] = typer.Option(None, "--file", help="Test file (repeatable)."),
    cls: list[str] = typer.Option(None, "--class", help="Test class (repeatable)."),
    marker: list[str] = typer.Option(None, "--marker", "-m", help="Marker (repeatable)."),
    keyword: str = typer.Option(None, "--keyword", "-k"),
    all_: bool = typer.Option(False, "--all", help="Select every discovered case."),
    workers: int = typer.Option(None, "--workers", "-n", help="Max workers (default from config)."),
    strategy: str = typer.Option(None, "--strategy", help="case|class|file|resource"),
    reruns: int = typer.Option(0, "--reruns", help="Retry failures N times."),
    timeout: float = typer.Option(None, "--timeout", help="Per-test timeout in seconds."),
    headless: bool = typer.Option(True, "--headless/--headed"),
    browser: str = typer.Option(None, "--browser", help="chromium|firefox|webkit."),
    fail_fast: bool = typer.Option(False, "--fail-fast", "-x"),
    open_report: bool = typer.Option(False, "--open", help="Open Allure report afterwards."),
    extra: list[str] = typer.Option(None, "--extra", help="Extra pytest argument (repeatable)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run a batch of selected cases with a unified Run ID."""
    svc = _service(root)
    discovery = svc.discover()
    cases, unknown = select_cases(discovery, nodeid, file, cls, marker, keyword, all_)
    if unknown:
        err_console.print(f"[red]unknown nodeids (aborting, not silently skipped):[/] {unknown}")
        raise typer.Exit(code=2)
    if not cases:
        err_console.print("[red]no cases selected. Use --nodeid/--file/--class/--marker/--all.[/]")
        raise typer.Exit(code=2)

    cfg = RunConfig(
        project_root=str(svc.root),
        workers=workers or svc.config.scheduling.default_workers,
        strategy=_strategy(strategy) or svc.config.scheduling.default_strategy,
        markers=marker or [],
        keyword=keyword,
        headless=headless,
        reruns=reruns,
        timeout=timeout,
        browser=browser or svc.config.browser.browser,
        extra_args=extra or [],
        open_report=open_report,
        fail_fast=fail_fast,
    )
    manifest = _execute_with_progress(svc, cases, cfg, as_json)
    raise typer.Exit(code=0 if manifest.state.value == "passed" else 1)


def _execute_with_progress(
    svc: ProjectService, cases, cfg: RunConfig, as_json: bool
) -> RunManifest:  # noqa: ANN001
    if not as_json:
        preview = svc.plan(cases, cfg)
        console.print(Panel("\n".join(preview.summary_lines()), title="Schedule preview (planned)"))

    def on_event(event: RunEvent) -> None:
        if as_json:
            return
        if event.type == "report" and event.when == "call":
            icon = {
                "passed": "[green]PASS[/]",
                "failed": "[red]FAIL[/]",
                "skipped": "[yellow]SKIP[/]",
            }.get(event.outcome or "", event.outcome or "")
            extra = f" [red]{event.error_type}[/]" if event.error_type else ""
            console.print(f"  {icon} {esc(event.nodeid)} [dim]({event.worker})[/]{extra}")
        elif event.type == "evidence" and event.path:
            console.print(f"    [magenta]screenshot[/] {event.path}")

    console.print("[bold]Running...[/]")
    manifest = svc.run(cases, cfg, on_event=on_event)

    if as_json:
        console.print_json(manifest.model_dump_json())
    else:
        _print_manifest_summary(manifest)
    return manifest


def _print_manifest_summary(manifest: RunManifest) -> None:
    c = manifest.counts
    table = Table(title=f"Run {manifest.run_id} — {manifest.state.value.upper()}")
    table.add_column("passed", style="green")
    table.add_column("failed", style="red")
    table.add_column("flaky", style="yellow")
    table.add_column("skipped")
    table.add_column("duration")
    table.add_row(
        str(c.passed),
        str(c.failed + c.error),
        str(c.flaky),
        str(c.skipped),
        f"{manifest.duration:.1f}s",
    )
    console.print(table)
    console.print(f"run dir : {manifest.run_dir}")
    if manifest.allure_report:
        console.print(f"allure  : {manifest.allure_report}")
    if manifest.report_error:
        err_console.print(f"[red]report generation error:[/] {manifest.report_error}")
    if manifest.failed_nodeids:
        console.print("[bold red]failed:[/]")
        for nid in manifest.failed_nodeids:
            r = manifest.results[nid]
            console.print(f"  - {esc(nid)} [dim]({r.error_type or 'failure'})[/]")


# --------------------------------------------------------------------------- runs
@app.command()
def runs(
    root: Path = ROOT_OPTION,
    limit: int = typer.Option(15, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List past runs."""
    svc = _service(root)
    items = svc.list_runs()[-limit:]
    if as_json:
        console.print_json(json.dumps([i.model_dump(mode="json") for i in items]))
        return
    table = Table(title="Runs")
    for col in ("run id", "state", "total", "passed", "failed", "flaky", "duration", "report"):
        table.add_column(col)
    for r in reversed(items):
        table.add_row(
            r.run_id,
            r.state.value,
            str(r.total),
            str(r.passed),
            str(r.failed),
            str(r.flaky),
            f"{r.duration:.1f}s",
            "yes" if r.report_exists else "-",
        )
    console.print(table)


def _resolve_run_id(svc: ProjectService, run_id: str | None, latest: bool) -> str:
    rid = run_id
    if latest or not rid:
        rid = svc.latest_run_id()
    if not rid:
        err_console.print("[red]no runs found[/]")
        raise typer.Exit(code=2)
    return rid


# ----------------------------------------------------------------------- failures
@app.command()
def failures(
    run_id: str = typer.Argument(None),
    root: Path = ROOT_OPTION,
    latest: bool = typer.Option(False, "--latest"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show failures of a run with location, error and evidence."""
    svc = _service(root)
    rid = _resolve_run_id(svc, run_id, latest)
    manifest = svc.load_run(rid)
    failed = [manifest.results[n] for n in manifest.failed_nodeids]
    if as_json:
        console.print_json(json.dumps([r.model_dump(mode="json") for r in failed]))
        return
    if not failed:
        console.print(f"[green]run {rid} has no failures[/]")
        return
    for r in failed:
        console.print(
            Panel.fit(
                f"[bold]{esc(r.nodeid)}[/]\n"
                f"status  : {r.status.value}\n"
                f"error   : {r.error_type or 'failure'} ({r.failure_phase or '?'})\n"
                f"worker  : {r.worker}  attempts: {r.attempts}\n"
                f"shots   : {', '.join(r.screenshots) or '-'}\n"
                f"traces  : {', '.join(r.traces) or '-'}\n"
                f"videos  : {', '.join(r.videos) or '-'}",
                title="failure",
            )
        )
        if r.longrepr:
            console.print(r.longrepr[:1500], markup=False)


# -------------------------------------------------------------------------- rerun
@app.command()
def rerun(
    run_id: str = typer.Argument(None),
    root: Path = ROOT_OPTION,
    latest: bool = typer.Option(False, "--latest"),
    nodeid: list[str] = typer.Option(None, "--nodeid", help="Rerun specific nodeids."),
    open_report: bool = typer.Option(False, "--open"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Rerun failed cases using exact nodeids and the original environment."""
    svc = _service(root)
    rid = _resolve_run_id(svc, run_id, latest)
    manifest = svc.rerun(rid, nodeids=nodeid or None, on_event=None)
    if open_report and manifest.allure_report:
        allure_mod.open_report(Path(manifest.allure_report))
    if as_json:
        console.print_json(manifest.model_dump_json())
    else:
        _print_manifest_summary(manifest)
    raise typer.Exit(code=0 if manifest.state.value == "passed" else 1)


# ------------------------------------------------------------------------- report
@app.command()
def report(
    run_id: str = typer.Argument(None),
    root: Path = ROOT_OPTION,
    latest: bool = typer.Option(False, "--latest"),
    open_: bool = typer.Option(False, "--open"),
    generate: bool = typer.Option(False, "--generate", help="Regenerate before opening."),
) -> None:
    """Generate and/or open the Allure report for a run."""
    svc = _service(root)
    rid = _resolve_run_id(svc, run_id, latest)
    if generate:
        ok, detail = svc.generate_report(rid, open_after=open_)
    elif open_:
        ok, detail = svc.open_report(rid)
    else:
        manifest = svc.load_run(rid)
        ok = bool(manifest.allure_report and Path(manifest.allure_report).exists())
        detail = manifest.allure_report or manifest.report_error or "no report"
    (console if ok else err_console).print(("[green]" if ok else "[red]") + detail)


# -------------------------------------------------------------------------- debug
@app.command()
def debug(
    root: Path = ROOT_OPTION,
    nodeid: list[str] = typer.Option(None, "--nodeid", help="Test nodeid to debug."),
    file: str = typer.Option(None, "--file", help="Debug a whole test file."),
    latest: bool = typer.Option(False, "--latest", help="Use failures from the latest run."),
) -> None:
    """Write a Zed Debugpy configuration for a test and print how to start it."""
    root = root.resolve()
    targets = list(nodeid or [])
    if latest and not targets:
        svc = _service(root)
        rid = svc.latest_run_id()
        if rid:
            targets = svc.load_run(rid).failed_nodeids
    if not targets and file:
        targets = [file]
    if not targets:
        # Still install the generic profiles (active file / symbol / --lf).
        path = zed_integration.install_debug(root)
        console.print(f"[green]wrote generic Debugpy profiles to[/] {path}")
    else:
        for nid in targets:
            path = zed_integration.add_debug_for_nodeid(root, nid)
            console.print(f"[green]added debug config[/] {esc(nid)} -> {path}")
    console.print(
        Panel(
            "In Zed: open the [bold]Debugger[/] panel (Ctrl+Shift+D or 'debugger: start'),\n"
            "pick the [bold]zedqa: Debug ...[/] configuration, set breakpoints, press play.\n"
            "xdist and reruns are disabled so the debugger attaches to the right process.",
            title="How to debug",
        )
    )


# ------------------------------------------------------------------------- suites
@suites_app.command("list")
def suites_list(root: Path = ROOT_OPTION) -> None:
    svc = _service(root)
    if not svc.config.suites:
        console.print("[yellow]no saved suites[/]")
        return
    for name, s in svc.config.suites.items():
        kind = "nodeids" if s.nodeids else f"filter={s.filter}"
        console.print(
            f"[bold]{name}[/]: {len(s.nodeids)} nodeids ({kind})"
            if s.nodeids
            else f"[bold]{name}[/]: {kind}"
        )


@suites_app.command("create")
def suites_create(
    name: str,
    root: Path = ROOT_OPTION,
    nodeid: list[str] = typer.Option(None, "--nodeid"),
    file: list[str] = typer.Option(None, "--file"),
    cls: list[str] = typer.Option(None, "--class"),
    marker: list[str] = typer.Option(None, "--marker"),
    keyword: str = typer.Option(None, "--keyword"),
    all_: bool = typer.Option(False, "--all"),
    description: str = typer.Option(None, "--description"),
) -> None:
    """Create a suite from explicit nodeids or a dynamic filter."""
    svc = _service(root)
    if not any((nodeid, file, cls, marker, keyword, all_)):
        err_console.print(
            "[red]provide --nodeid or a filter (--file/--class/--marker/--keyword/--all)[/]"
        )
        raise typer.Exit(code=2)
    if nodeid:
        suite = Suite(name=name, nodeids=nodeid, description=description)
    else:
        discovery = svc.discover()
        cases, unknown = select_cases(discovery, nodeid, file, cls, marker, keyword, all_)
        if unknown:
            err_console.print(f"[yellow]unknown nodeids:[/] {unknown}")
        suite = Suite(name=name, nodeids=[c.nodeid for c in cases], description=description)
    svc.save_suite(suite)
    console.print(f"[green]saved suite[/] {name} ({len(suite.nodeids)} cases)")


@suites_app.command("show")
def suites_show(name: str, root: Path = ROOT_OPTION) -> None:
    svc = _service(root)
    suite = svc.config.suites.get(name)
    if not suite:
        err_console.print(f"[red]no suite '{name}'[/]")
        raise typer.Exit(code=2)
    discovery = svc.discover()
    cases, missing = svc.resolve_suite(suite, discovery)
    console.print(f"[bold]{name}[/]: {len(cases)} resolved, {len(missing)} missing")
    for c in cases:
        console.print(f"  {esc(c.nodeid)}")
    for m in missing:
        err_console.print(f"[red]missing:[/] {esc(m)}")


@suites_app.command("delete")
def suites_delete(name: str, root: Path = ROOT_OPTION) -> None:
    svc = _service(root)
    console.print("deleted" if svc.delete_suite(name) else "not found")


@suites_app.command("run")
def suites_run(
    name: str,
    root: Path = ROOT_OPTION,
    workers: int = typer.Option(None, "--workers", "-n"),
    strategy: str = typer.Option(None, "--strategy"),
    open_report: bool = typer.Option(False, "--open"),
) -> None:
    """Resolve a suite and run it. Missing nodeids are reported, never substituted."""
    svc = _service(root)
    suite = svc.config.suites.get(name)
    if not suite:
        err_console.print(f"[red]no suite '{name}'[/]")
        raise typer.Exit(code=2)
    discovery = svc.discover()
    cases, missing = svc.resolve_suite(suite, discovery)
    if missing:
        err_console.print(
            f"[yellow]suite has {len(missing)} missing nodeid(s):[/] {[escape(str(m)) for m in missing]}"
        )
    if not cases:
        err_console.print("[red]suite resolved to no cases[/]")
        raise typer.Exit(code=2)
    cfg = RunConfig(
        project_root=str(svc.root),
        workers=workers or svc.config.scheduling.default_workers,
        strategy=_strategy(strategy) or svc.config.scheduling.default_strategy,
        open_report=open_report,
    )
    manifest = svc.run(cases, cfg)
    _print_manifest_summary(manifest)
    raise typer.Exit(code=0 if manifest.state.value == "passed" else 1)


# ----------------------------------------------------------------------------- ui
@app.command()
def ui(root: Path = ROOT_OPTION) -> None:
    """Launch the interactive Terminal UI (Test Explorer + live monitor)."""
    from zedqa.tui.app import ZedqaTUI

    ZedqaTUI(root.resolve()).run()


# -------------------------------------------------------------------------- status
@app.command()
def status(root: Path = ROOT_OPTION) -> None:
    """One-line project and toolchain status."""
    svc = _service(root)
    cfg = load_config(svc.root)
    console.print(f"root       : {svc.root}")
    console.print(f"interpreter: {svc.interpreter}")
    console.print(
        f"strategy   : {cfg.scheduling.default_strategy.value} ({cfg.scheduling.default_workers} workers)"
    )
    console.print(f"resource rules: {len(cfg.resources.rules)}")
    latest = svc.latest_run_id()
    console.print(f"latest run : {latest or '-'}")


if __name__ == "__main__":  # pragma: no cover
    app()
