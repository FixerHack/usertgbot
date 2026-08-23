"""Backup/restore for reputation_records, source-agnostic.

Backups are always plain JSON snapshots on disk, regardless of which
DataSource (Postgres, SQLite, CSV, JSON) they were taken from — that's
also exactly the shape db_maintenance.sources.json_source.JSONSource
knows how to read, so a backup file can itself be re-checked/re-fixed
as a JSON source if needed.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from db_maintenance.config import settings
from db_maintenance.sources.base import DataSource


@dataclass
class BackupHandle:
    source_label: str
    path: Path
    created_at: str
    row_count: int


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


async def backup_records(source: DataSource) -> BackupHandle:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    rows = await source.load_records()

    created_at = datetime.now(timezone.utc)
    path = settings.backup_dir / f"{source.label}_{created_at.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(
        json.dumps(
            {"source": source.label, "created_at": created_at.isoformat(), "rows": rows},
            default=_default,
        ),
        encoding="utf-8",
    )
    return BackupHandle(source_label=source.label, path=path, created_at=created_at.isoformat(), row_count=len(rows))


def latest_backup(source_label: str) -> Path | None:
    if not settings.backup_dir.exists():
        return None
    candidates = sorted(settings.backup_dir.glob(f"{source_label}_*.json"))
    return candidates[-1] if candidates else None


async def restore_records(source: DataSource, backup_path: Path | None = None) -> int:
    path = backup_path or latest_backup(source.label)
    if path is None:
        raise FileNotFoundError(f"No backup found for source '{source.label}' in {settings.backup_dir}")

    data = json.loads(path.read_text(encoding="utf-8"))
    return await source.replace_all(data["rows"])
