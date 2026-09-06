"""Per-worker runtime state, shared across a session's handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from shared.notify import ManagerNotifier
from userbot.message_cache import RecentMessageCache


@dataclass
class WorkerContext:
    session_id: int
    owner_user_id: int              # internal db.User.id of the account owner
    owner_telegram_id: int | None = None  # owner's Telegram id, for manager-bot DMs
    owner_lang: str = "uk"          # owner's language for notifications (i18n)
    notifier: ManagerNotifier | None = None
    # RecentMessageCache needs owner_user_id at construction time (SQLite rows
    # are scoped per owner) — built explicitly in worker.py, no default here.
    cache: RecentMessageCache = None  # type: ignore[assignment]
    # sender_id -> monotonic ts of last auto-reply, for cooldown
    autoresponder_last: dict[int, float] = field(default_factory=dict)
    # (chat_id, user_id) -> until (None = until .unmute). Checked on every
    # incoming message, so it lives here rather than behind a query.
    muted: dict[tuple[int, int], datetime | None] = field(default_factory=dict)
    # chat_id -> recording id, for the same reason: the collector runs on
    # every message in every chat.
    recording: dict[int, int] = field(default_factory=dict)
    # (chat_id, user_id) pairs being echoed back — same reason again.
    cloned: set[tuple[int, int]] = field(default_factory=set)
