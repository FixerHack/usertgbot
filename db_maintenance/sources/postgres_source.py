"""Default source: the live Postgres DB shared with management_bot/userbot."""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from db.models import ReputationRecord, User
from db.session import get_session
from db_maintenance.sources.base import DataSource

_COLUMNS = (
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


class PostgresSource(DataSource):
    label = "postgres"

    async def load_records(self) -> list[dict[str, Any]]:
        async with get_session() as session:
            rows = (await session.execute(select(ReputationRecord))).scalars().all()
            return [{col: getattr(row, col) for col in _COLUMNS} for row in rows]

    async def load_user_ids(self) -> set[Any] | None:
        async with get_session() as session:
            ids = (await session.execute(select(User.id))).scalars().all()
            return set(ids)

    async def apply_fixes(self, *, delete_ids: list[Any], updates: dict[Any, dict[str, Any]]) -> None:
        async with get_session() as session:
            if delete_ids:
                await session.execute(delete(ReputationRecord).where(ReputationRecord.id.in_(delete_ids)))
            for row_id, fields in updates.items():
                await session.execute(update(ReputationRecord).where(ReputationRecord.id == row_id).values(**fields))
            await session.commit()

    async def replace_all(self, rows: list[dict[str, Any]]) -> int:
        async with get_session() as session:
            await session.execute(delete(ReputationRecord))
            if rows:
                clean_rows = [_coerce_row(row) for row in rows]
                await session.execute(ReputationRecord.__table__.insert(), clean_rows)
            await session.commit()
        return len(rows)


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    """Backups (and CSV/JSON sources) store timestamps as ISO strings —
    asyncpg needs real datetime objects for timestamp columns."""
    clean = {k: v for k, v in row.items() if k in _COLUMNS}
    for field in ("created_at", "updated_at"):
        value = clean.get(field)
        if isinstance(value, str):
            clean[field] = datetime.fromisoformat(value)
    return clean
