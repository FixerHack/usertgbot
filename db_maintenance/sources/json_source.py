"""JSON export of reputation_records — also what db_maintenance's own
backups look like, so a backup file is itself valid input for this source.

Accepted shapes on read:
  1. a bare list of reputation records: [{"id": 1, "user_id": 1, ...}, ...]
     -> orphan checks skipped (no users list)
  2. {"rows": [...]}                      (db_maintenance's own backup shape)
     -> orphan checks skipped
  3. {"reputation_records": [...], "users": [{"id": 1, ...}, ...]}
     -> orphan checks enabled against the given users list

Whichever shape is read is the shape written back; only the
reputation_records list is ever mutated.
"""

import json
from pathlib import Path
from typing import Any

from db_maintenance.sources.base import DataSource


class JSONSource(DataSource):
    def __init__(self, path: Path, *, id_field: str = "id") -> None:
        self.path = path
        self.id_field = id_field
        self.label = f"json_{path.stem}"

    def _read(self) -> Any:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _extract(self, data: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        if isinstance(data, list):
            return data, None
        if isinstance(data, dict):
            if "reputation_records" in data:
                return data["reputation_records"], data.get("users")
            if "rows" in data:
                return data["rows"], None
        raise ValueError(f"Unrecognised JSON shape in {self.path}")

    async def load_records(self) -> list[dict[str, Any]]:
        records, _ = self._extract(self._read())
        return records

    async def load_user_ids(self) -> set[Any] | None:
        _, users = self._extract(self._read())
        if users is None:
            return None
        return {u["id"] for u in users}

    def _write(self, records: list[dict[str, Any]]) -> None:
        data = self._read()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, list):
            self.path.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        elif "reputation_records" in data:
            data["reputation_records"] = records
            self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        else:
            data["rows"] = records
            self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    async def apply_fixes(self, *, delete_ids: list[Any], updates: dict[Any, dict[str, Any]]) -> None:
        records = await self.load_records()
        delete_set = set(delete_ids)
        kept = [r for r in records if r.get(self.id_field) not in delete_set]
        for row in kept:
            if row.get(self.id_field) in updates:
                row.update(updates[row[self.id_field]])
        self._write(kept)

    async def replace_all(self, rows: list[dict[str, Any]]) -> int:
        self._write(rows)
        return len(rows)
