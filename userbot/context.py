"""Per-worker runtime state, shared across a session's handlers."""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.notify import ManagerNotifier
from userbot.message_cache import RecentMessageCache


@dataclass
class WorkerContext:
    session_id: int
    owner_user_id: int              # internal db.User.id of the account owner
    owner_telegram_id: int | None = None  # owner's Telegram id, for manager-bot DMs
    owner_lang: str = "uk"          # owner's language for notifications (i18n)
    notifier: ManagerNotifier | None = None
    cache: RecentMessageCache = field(default_factory=RecentMessageCache)
    # sender_id -> monotonic ts of last auto-reply, for cooldown
    autoresponder_last: dict[int, float] = field(default_factory=dict)
