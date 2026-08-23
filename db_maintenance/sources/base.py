"""Abstract data source for db_maintenance.

check/fix/restore work against plain dicts, not SQLAlchemy models, so the
exact same logic runs unchanged against Postgres, a local SQLite file, a
CSV file, or a JSON file. `user_id` on a reputation record always refers to
a `users[].id` from the same source — never a raw Telegram id — matching
db.models' FK semantics.
"""

from abc import ABC, abstractmethod
from typing import Any


class DataSource(ABC):
    #: short, filesystem-safe tag used in backup filenames and log lines
    label: str
    #: which field is the row's primary key (profiles may override, e.g.
    #: "telegram_id" for a contacts CSV). DB sources stay "id".
    id_field: str = "id"

    @abstractmethod
    async def load_records(self) -> list[dict[str, Any]]:
        """All reputation_records rows as plain dicts (must include 'id')."""

    @abstractmethod
    async def load_user_ids(self) -> set[Any] | None:
        """Valid user ids for orphan detection.

        None means this source can't tell (e.g. a bare CSV/JSON of
        reputation rows with no accompanying users list) — callers must
        skip orphan checks rather than flag everything as orphaned.
        """

    @abstractmethod
    async def apply_fixes(self, *, delete_ids: list[Any], updates: dict[Any, dict[str, Any]]) -> None:
        """Delete rows by id and patch fields by id, as one batch."""

    @abstractmethod
    async def replace_all(self, rows: list[dict[str, Any]]) -> int:
        """Used by `restore`: wipe reputation_records and reload from a backup snapshot."""
