"""Per-user flood control for the Bot API services.

An outer middleware that silently drops updates from a user sending them
faster than the allowed rate — a fixed-size in-memory sliding window per
Telegram user id, no DB access, so it costs almost nothing and (crucially)
runs *before* any DB-backed middleware, keeping a flood of taps from a single
account off the database and handlers entirely.

This is app-level flood control, not a substitute for infra-level DDoS
protection (that belongs at a reverse proxy / firewall) — it raises the bar
for "spam our own bot's commands/buttons to make it do expensive work", which
is the realistic abuse case for a small Bot-API service with no public HTTP
endpoint of its own.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, *, max_events: int = 5, window_seconds: float = 3.0) -> None:
        self._max_events = max_events
        self._window = window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        hits = self._hits[user.id]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._max_events:
            # still ack callback queries so a throttled tap doesn't leave the
            # button stuck in Telegram's client-side loading spinner
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    pass
            return None
        hits.append(now)
        return await handler(event, data)
