"""CLI entrypoint for the standalone db_maintenance service.

Run independently: `uv run db-maintenance <command>`
Manual invocation or from cron — this process never runs as part of
management_bot or userbot.
"""

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from db_maintenance.commands.check import CheckReport, run_check
from db_maintenance.commands.fix import run_fix
from db_maintenance.commands.restore import run_restore
from db_maintenance.commands.stats import run_stats
from db_maintenance.logging_conf import setup_logging
from db_maintenance.metrics.store import RunMetrics, append, read_last
from db_maintenance.profiles import PROFILES, CheckConfig, get_profile
from db_maintenance.report import render_html
from db_maintenance.sources.base import DataSource
from db_maintenance.sources.factory import resolve_source

app = typer.Typer(help="Standalone DB maintenance CLI (independent of management_bot/userbot).")
logger = setup_logging()

SOURCE_OPTION = typer.Option(
    None,
    "--source",
    help="Postgres (default, via DB_URL) unless a file is given: .db/.sqlite/.sqlite3, .csv, or .json",
)
PROFILE_OPTION = typer.Option(
    None,
    "--profile",
    help=f"Check profile defining what 'broken' means. Available: {', '.join(PROFILES)} (default: reputation)",
)
COLUMNS_OPTION = typer.Option(
    None,
    "--columns",
    help="Comma-separated column names for a HEADERLESS csv (overrides the profile's columns)",
)


def _resolve(source: Optional[str], profile: Optional[str], columns: Optional[str]) -> tuple[DataSource, CheckConfig]:
    config = get_profile(profile)
    cols = tuple(c.strip() for c in columns.split(",")) if columns else config.csv_columns
    src = resolve_source(source, id_field=config.id_field, columns=cols)
    return src, config


def _record_metrics(command: str, source_label: str, duration: float, report: CheckReport, fixed: int, deleted: int) -> None:
    append(
        RunMetrics(
            started_at=datetime.now(timezone.utc).isoformat(),
            command=command,
            duration_seconds=duration,
            scanned=report.scanned,
            broken=report.broken_count,
            fixed=fixed,
            deleted=deleted,
            source=source_label,
        )
    )


@app.command()
def check(
    source: Optional[str] = SOURCE_OPTION,
    profile: Optional[str] = PROFILE_OPTION,
    columns: Optional[str] = COLUMNS_OPTION,
) -> None:
    """Scan a source and report broken/incomplete/duplicate records."""

    src, config = _resolve(source, profile, columns)

    async def _run() -> CheckReport:
        return await run_check(src, config)

    started = time.monotonic()
    report = asyncio.run(_run())
    duration = time.monotonic() - started

    label = src.label
    _record_metrics("check", label, duration, report, fixed=0, deleted=0)

    logger.info(
        "check finished: source=%s profile=%s scanned=%s broken=%s",
        label, config.name, report.scanned, report.broken_count,
    )
    typer.echo(f"[{label}/{config.name}] scanned: {report.scanned}, broken: {report.broken_count}")
    if config.orphan_field and not report.orphan_check_supported:
        typer.echo("  (orphan check skipped: this source has no users list)")


@app.command()
def fix(
    source: Optional[str] = SOURCE_OPTION,
    profile: Optional[str] = PROFILE_OPTION,
    columns: Optional[str] = COLUMNS_OPTION,
) -> None:
    """Auto-repair issues found by `check`, backing up first."""

    src, config = _resolve(source, profile, columns)

    async def _run():
        report = await run_check(src, config)
        fix_report = await run_fix(src, report, config)
        return report, fix_report

    started = time.monotonic()
    report, fix_report = asyncio.run(_run())
    duration = time.monotonic() - started

    label = src.label
    _record_metrics("fix", label, duration, report, fixed=fix_report.fixed, deleted=fix_report.deleted)

    logger.info("fix finished: source=%s fixed=%s deleted=%s", label, fix_report.fixed, fix_report.deleted)
    typer.echo(f"[{label}] fixed: {fix_report.fixed}, deleted: {fix_report.deleted}")


@app.command()
def restore(source: Optional[str] = SOURCE_OPTION) -> None:
    """Roll a source back to its latest backup."""
    src = resolve_source(source)
    row_count = asyncio.run(run_restore(src))
    logger.info("restore finished: source=%s rows=%s", src.label, row_count)
    typer.echo(f"[{src.label}] restored from latest backup: {row_count} row(s).")


@app.command()
def stats(n: int = typer.Option(10, "--last", help="Number of recent runs to summarize")) -> None:
    """Show a summary/trend of the last N maintenance runs."""
    typer.echo(run_stats(n))


@app.command()
def report(
    out: Path = typer.Option(Path("metrics/report.html"), "--out", help="Where to write the HTML report"),
    n: int = typer.Option(0, "--last", help="Only include the last N runs (0 = all)"),
) -> None:
    """Render metrics/db_maintenance.jsonl as a self-contained HTML report."""
    runs = read_last(n or 10_000_000)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(runs), encoding="utf-8")
    logger.info("report written: path=%s runs=%s", out, len(runs))
    typer.echo(f"Wrote {out} ({len(runs)} run(s)).")


@app.command()
def profiles() -> None:
    """List available check profiles and what each one validates."""
    for name, config in PROFILES.items():
        typer.echo(f"• {name}")
        typer.echo(f"    id field:     {config.id_field}")
        typer.echo(f"    required:     {', '.join(config.required_fields) or '—'}")
        typer.echo(f"    duplicate by: {', '.join(config.duplicate_keys) or '—'}")
        typer.echo(f"    orphan field: {config.orphan_field or '—'}")
        if config.csv_columns:
            typer.echo(f"    csv columns:  {', '.join(config.csv_columns)}")


if __name__ == "__main__":
    app()
