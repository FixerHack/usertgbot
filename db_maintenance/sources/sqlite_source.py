"""Local SQLite (.db/.sqlite/.sqlite3) mirror of the reputation schema.

Handy for checking/fixing an offline export, or for testing db_maintenance
without a Postgres server running at all. Expects (and creates, if the
file is new) two tables with the same shape as db.models:
  users(id INTEGER PRIMARY KEY, telegram_id, ...)
  reputation_records(id INTEGER PRIMARY KEY, user_id, chat_id, score,
                      positive_count, negative_count,
                      reported_by_telegram_id, created_at, updated_at)

If the file has a reputation_records table but no users table, orphan
detection is skipped (load_user_ids returns None) rather than guessing.
"""

import sqlite3
from pathlib import Path
from typing import Any

from db_maintenance.sources.base import DataSource

_REPUTATION_COLUMNS = (
    "id",
    "user_id",
    "chat_id",
    "score",
    "positive_count",
    "negative_count",
    "reported_by_telegram_id",
    "created_at",
    "updated_at",
)

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER UNIQUE,
    username TEXT,
    full_name TEXT,
    is_blocked INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
)
"""

_CREATE_REPUTATION = """
CREATE TABLE IF NOT EXISTS reputation_records (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    positive_count INTEGER,
    negative_count INTEGER,
    reported_by_telegram_id INTEGER,
    created_at TEXT,
    updated_at TEXT
)
"""


class SQLiteSource(DataSource):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.label = f"sqlite_{path.stem}"

    def _connect(self) -> sqlite3.Connection:
        is_new = not self.path.exists()
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        if is_new:
            conn.execute(_CREATE_USERS)
            conn.execute(_CREATE_REPUTATION)
            conn.commit()
        return conn

    def _has_users_table(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        return row is not None

    async def load_records(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT {', '.join(_REPUTATION_COLUMNS)} FROM reputation_records").fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    async def load_user_ids(self) -> set[Any] | None:
        conn = self._connect()
        try:
            if not self._has_users_table(conn):
                return None
            rows = conn.execute("SELECT id FROM users").fetchall()
            return {row["id"] for row in rows}
        finally:
            conn.close()

    async def apply_fixes(self, *, delete_ids: list[Any], updates: dict[Any, dict[str, Any]]) -> None:
        conn = self._connect()
        try:
            if delete_ids:
                placeholders = ",".join("?" * len(delete_ids))
                conn.execute(f"DELETE FROM reputation_records WHERE id IN ({placeholders})", delete_ids)
            for row_id, fields in updates.items():
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE reputation_records SET {set_clause} WHERE id = ?",
                    (*fields.values(), row_id),
                )
            conn.commit()
        finally:
            conn.close()

    async def replace_all(self, rows: list[dict[str, Any]]) -> int:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM reputation_records")
            for row in rows:
                clean = {k: row.get(k) for k in _REPUTATION_COLUMNS}
                columns = ", ".join(clean.keys())
                placeholders = ", ".join("?" * len(clean))
                conn.execute(
                    f"INSERT INTO reputation_records ({columns}) VALUES ({placeholders})",
                    tuple(clean.values()),
                )
            conn.commit()
            return len(rows)
        finally:
            conn.close()
