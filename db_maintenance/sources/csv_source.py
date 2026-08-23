"""Schema-agnostic CSV source.

Reads whatever columns the file has (values kept as strings — `check` treats
empty strings as blank, so no coercion is needed). Two flexibility knobs:

* `columns`  — for a *headerless* CSV, the column names to assign positionally
               (e.g. a raw contacts dump). When given, the file is read and
               written back without a header row.
* `id_field` — which column is the primary key for fix/restore (default "id";
               a contacts profile uses "telegram_id").

Orphan detection uses a sibling `<name>.users.csv` (column `id`) if present,
otherwise it's skipped.
"""

import csv
from pathlib import Path
from typing import Any

from db_maintenance.sources.base import DataSource


class CSVSource(DataSource):
    def __init__(self, path: Path, *, id_field: str = "id", columns: tuple[str, ...] | None = None) -> None:
        self.path = path
        self.id_field = id_field
        self.columns = list(columns) if columns else None
        self.headerless = columns is not None
        self.users_path = path.with_suffix("").with_suffix(".users.csv")
        self.label = f"csv_{path.stem}"

    async def load_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as f:
            if self.headerless:
                reader = csv.reader(f)
                return [self._row_from_values(values) for values in reader]
            return [dict(raw) for raw in csv.DictReader(f)]

    def _row_from_values(self, values: list[str]) -> dict[str, Any]:
        # Tolerate ragged rows: pad missing, ignore extras beyond declared columns.
        row: dict[str, Any] = {}
        for i, name in enumerate(self.columns):
            row[name] = values[i] if i < len(values) else None
        return row

    async def load_user_ids(self) -> set[Any] | None:
        if not self.users_path.exists():
            return None
        with self.users_path.open(newline="", encoding="utf-8") as f:
            return {row["id"] for row in csv.DictReader(f)}

    def _fieldnames(self, rows: list[dict[str, Any]]) -> list[str]:
        if self.columns:
            return list(self.columns)
        return list(rows[0].keys()) if rows else []

    async def _write_all(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = self._fieldnames(rows)
        with self.path.open("w", newline="", encoding="utf-8") as f:
            if self.headerless:
                writer = csv.writer(f)
                for row in rows:
                    writer.writerow([row.get(col, "") for col in fieldnames])
            else:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow({col: row.get(col, "") for col in fieldnames})

    async def apply_fixes(self, *, delete_ids: list[Any], updates: dict[Any, dict[str, Any]]) -> None:
        rows = await self.load_records()
        delete_set = set(delete_ids)
        kept = [r for r in rows if r.get(self.id_field) not in delete_set]
        for row in kept:
            if row.get(self.id_field) in updates:
                row.update(updates[row[self.id_field]])
        await self._write_all(kept)

    async def replace_all(self, rows: list[dict[str, Any]]) -> int:
        await self._write_all(rows)
        return len(rows)
