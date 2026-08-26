"""Outer middlewares for management_bot, run before any handler/router.

`LangAndBlockMiddleware` does two things per update, from one DB lookup:
1. Enforces `User.is_blocked` (set from the admin panel) — a blocked user gets
   a fixed notice and no handler ever runs, instead of the bot answering
   normally as if nothing happened.
2. Publishes the DB-resolved language (respecting a manual `/settings` choice,
   see `db.queries.set_user_language_manual`) into `shared.i18n`'s per-update
   contextvar, so every existing `lang_of(event)` call in the handlers below
   picks it up automatically — no call-site changes needed.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from db.session import get_session
from db import queries
from shared.i18n import reset_lang_override, resolve_lang, set_lang_override, t

logger = logging.getLogger(__name__)


def _chat_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery):
        return event.message.chat.id if event.message else event.from_user.id
    return None


class LangAndBlockMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = _chat_id(event)
        if chat_id is None:
            return await handler(event, data)

        async with get_session() as db:
            user = await queries.get_user_by_telegram_id(db, chat_id)

        lang = resolve_lang(user.language_code) if user else None

        if user is not None and user.is_blocked:
            notice = t(lang or "uk", "account_blocked")
            if isinstance(event, CallbackQuery):
                await event.answer(notice, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(notice)
            return None

        token = set_lang_override(lang)
        try:
            return await handler(event, data)
        finally:
            reset_lang_override(token)


class WatchConnectMiddleware(BaseMiddleware):
    """TEMPORARY diagnostic — chasing a real report where a customer's taps
    on the /connect code keypad briefly showed as "not handled" (no router
    matched at all) with no exception anywhere. Registered on `dp.update`
    (the outermost observer, before type-specific dispatch), so it sees
    every raw update — including kinds like `my_chat_member` that never
    reach a Message/CallbackQuery handler — for a small watch-list of
    telegram ids. Never logs message text or callback payload beyond the
    bare `callback_data` string (safe — our own `cc:<digit>`/`cc:submit`
    values, never the phone/2FA-password text a user types).

    Remove once the mystery is resolved.
    """

    WATCH_IDS = {5647513346}  # artyyzt

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = event  # this middleware is registered on dp.update, so `event` is the raw Update
        kind, chat_id, extra = self._describe(update)
        watched = chat_id in self.WATCH_IDS
        if watched:
            state = data.get("state")
            fsm_state = await state.get_state() if state else None
            logger.warning(
                "WATCH in  update_id=%s kind=%s chat_id=%s fsm_state=%s extra=%r",
                update.update_id, kind, chat_id, fsm_state, extra,
            )
        result = await handler(event, data)
        if watched:
            logger.warning(
                "WATCH out update_id=%s -> %s",
                update.update_id, "propagated/handled" if result is not None else "NOT HANDLED",
            )
        return result

    @staticmethod
    def _describe(update) -> tuple[str, int | None, str | None]:
        if update.message:
            m = update.message
            if m.contact:
                kind = "contact"
            elif m.photo:
                kind = "photo"
            elif m.text is not None:
                kind = "text"  # never log the text itself — could be the 2FA password
            else:
                kind = "other"
            return "message", m.chat.id, kind
        if update.callback_query:
            cq = update.callback_query
            chat_id = cq.message.chat.id if cq.message else cq.from_user.id
            return "callback_query", chat_id, cq.data
        if update.edited_message:
            return "edited_message", update.edited_message.chat.id, None
        if update.my_chat_member:
            return "my_chat_member", update.my_chat_member.chat.id, None
        return "other", None, None
