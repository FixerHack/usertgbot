"""Persistent (SQLite-backed) cache of recently-seen messages, per worker.

Telethon's MessageDeleted event carries only ids (no content), and
MessageEdited doesn't include the previous text — so to auto-save deleted/
edited messages we must remember what came through. This used to be a plain
in-memory dict: simple, but any restart (a deploy, a crash, a reboot) wiped
it completely, silently losing anything "in flight" between being received
and being deleted — confirmed live during testing, several real messages
were lost purely to restart timing. Backed by SQLite now so it survives
restarts; still bounded (oldest rows pruned past `max_size` per owner) so it
doesn't grow forever.

One shared SQLite file backs every connected account (each `RecentMessageCache`
instance is scoped to its own `owner_user_id` via the primary key, so workers
never see each other's rows) — simplest thing that survives a restart without
standing up a separate service (Redis, etc.); this project's scale doesn't
need more than that.

For non-channel chats (regular DMs and small groups), Telegram's delete
update (UpdateDeleteMessages) doesn't carry a peer at all, so Telethon
reports `event.chat_id = None` for those deletions — there is no
protocol-level way to know which chat a plain message_id belonged to.
`pop_by_message_id` is the fallback for that case: it looks up the most
recent row seen with that message_id, regardless of chat. Collisions are
possible (different chats can reuse the same id) but rare in practice, and
far better than never detecting DM deletions at all.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import aiosqlite


@dataclass
class CachedMessage:
    chat_id: int
    message_id: int
    sender_id: int | None
    text: str | None
    media: bytes | None = None
    media_kind: str | None = None  # "photo" | "voice" | "video" | "document", only set alongside `media`
    media_filename: str | None = None  # original filename, only meaningful for "document"
    location: tuple[float, float] | None = None  # (lat, long) — plain/live location or venue shares


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cached_messages (
    owner_user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sender_id INTEGER,
    text TEXT,
    media BLOB,
    media_kind TEXT,
    media_filename TEXT,
    location_lat REAL,
    location_lon REAL,
    created_at REAL NOT NULL,
    PRIMARY KEY (owner_user_id, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_cached_messages_by_msgid
    ON cached_messages(owner_user_id, message_id, created_at);
"""

_SELECT_COLUMNS = "chat_id, message_id, sender_id, text, media, media_kind, media_filename, location_lat, location_lon"


def _row_to_cached(row) -> CachedMessage:
    chat_id, message_id, sender_id, text, media, media_kind, media_filename, lat, lon = row
    location = (lat, lon) if lat is not None and lon is not None else None
    return CachedMessage(chat_id, message_id, sender_id, text, media, media_kind, media_filename, location)


class RecentMessageCache:
    """One instance per worker/connected account, scoped by `owner_user_id`.
    Lazily opens its SQLite connection on first use (can't do async work in
    `__init__`)."""

    def __init__(self, owner_user_id: int, db_path: str = "data/message_cache.sqlite3", max_size: int = 2000) -> None:
        self._owner_user_id = owner_user_id
        self._db_path = db_path
        self._max_size = max_size
        self._conn: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()

    async def _ensure_conn(self) -> aiosqlite.Connection:
        # Without the lock, two coroutines racing on the very first call (e.g.
        # a burst of incoming messages right after startup) could both see
        # `_conn is None`, both open a connection, and the loser's connection
        # leaks (never closed, never used again).
        if self._conn is None:
            async with self._connect_lock:
                if self._conn is None:
                    if self._db_path != ":memory:":
                        dirname = os.path.dirname(self._db_path)
                        if dirname:
                            os.makedirs(dirname, exist_ok=True)
                    conn = await aiosqlite.connect(self._db_path)
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.executescript(_SCHEMA)
                    await conn.commit()
                    self._conn = conn
        return self._conn

    async def remember(
        self,
        chat_id: int,
        message_id: int,
        sender_id: int | None,
        text: str | None,
        *,
        media: bytes | None = None,
        media_kind: str | None = None,
        media_filename: str | None = None,
        location: tuple[float, float] | None = None,
    ) -> None:
        conn = await self._ensure_conn()
        lat, lon = location if location else (None, None)
        await conn.execute(
            """INSERT INTO cached_messages
                (owner_user_id, chat_id, message_id, sender_id, text, media, media_kind,
                 media_filename, location_lat, location_lon, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, chat_id, message_id) DO UPDATE SET
                    sender_id=excluded.sender_id, text=excluded.text, media=excluded.media,
                    media_kind=excluded.media_kind, media_filename=excluded.media_filename,
                    location_lat=excluded.location_lat, location_lon=excluded.location_lon,
                    created_at=excluded.created_at""",
            (
                self._owner_user_id, chat_id, message_id, sender_id, text,
                media, media_kind, media_filename, lat, lon, time.time(),
            ),
        )
        await conn.commit()
        await self._evict_old(conn)

    async def _evict_old(self, conn: aiosqlite.Connection) -> None:
        # rowid as a tiebreaker: time.time() resolution can coincide for two
        # inserts issued back-to-back, and a tie in ORDER BY alone would make
        # eviction/lookup order nondeterministic between otherwise-identical rows
        await conn.execute(
            """DELETE FROM cached_messages WHERE owner_user_id=? AND rowid NOT IN (
                   SELECT rowid FROM cached_messages WHERE owner_user_id=?
                   ORDER BY created_at DESC, rowid DESC LIMIT ?
               )""",
            (self._owner_user_id, self._owner_user_id, self._max_size),
        )
        await conn.commit()

    async def get(self, chat_id: int, message_id: int) -> CachedMessage | None:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM cached_messages WHERE owner_user_id=? AND chat_id=? AND message_id=?",
            (self._owner_user_id, chat_id, message_id),
        )
        row = await cursor.fetchone()
        return _row_to_cached(row) if row else None

    async def pop(self, chat_id: int, message_id: int) -> CachedMessage | None:
        item = await self.get(chat_id, message_id)
        if item is not None:
            conn = await self._ensure_conn()
            await conn.execute(
                "DELETE FROM cached_messages WHERE owner_user_id=? AND chat_id=? AND message_id=?",
                (self._owner_user_id, chat_id, message_id),
            )
            await conn.commit()
        return item

    async def pop_by_message_id(self, message_id: int) -> CachedMessage | None:
        """Fallback for deletions with no known chat_id (DMs/small groups) —
        resolves to whichever chat most recently saw this message_id."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM cached_messages WHERE owner_user_id=? AND message_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (self._owner_user_id, message_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        item = _row_to_cached(row)
        await conn.execute(
            "DELETE FROM cached_messages WHERE owner_user_id=? AND chat_id=? AND message_id=?",
            (self._owner_user_id, item.chat_id, message_id),
        )
        await conn.commit()
        return item

    async def count(self) -> int:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM cached_messages WHERE owner_user_id=?", (self._owner_user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
