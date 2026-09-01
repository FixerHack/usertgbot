"""Tells the user, in the chat, when their Mini App connect link ran out.

Without this the expiry is invisible from the chat's side: the button just
sits there, and the only way to find out it's dead is to tap it and read the
page. The bot saying so — with a way straight back into settings — closes
that loop.

Why a DB sweep and not an `asyncio.sleep(ttl)` scheduled at token creation:
a timer living in the process is exactly the mistake the old LoginManager
made (see shared/connect_tokens.py's docstring). A bot restart mid-attempt
would drop it silently and the user would be left waiting for a message that
can never arrive. The claim is atomic (UPDATE ... RETURNING), so a restart
mid-tick can at worst re-run a tick, never double-notify.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.models import User
from db.session import get_session
from shared import connect_tokens
from shared.i18n import resolve_lang, t

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 30


async def _notify(bot: Bot, row) -> None:
    async with get_session() as db:
        user = (
            await db.execute(select(User).where(User.telegram_id == row.telegram_id))
        ).scalar_one_or_none()
        lang = resolve_lang(user.language_code if user else None)

    # The Mini App button is dead now — tapping it only reaches the "expired"
    # page. Remove it for the same reason the success path does: a control
    # that can't do anything shouldn't stay in the chat.
    if row.message_id:
        try:
            await bot.delete_message(chat_id=row.chat_id, message_id=row.message_id)
        except Exception:
            logger.debug("connect_notices: could not delete button message", exc_info=True)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            # "set:back" renders the settings panel in place of this message,
            # so the button lands the user exactly where they can retry.
            InlineKeyboardButton(text=t(lang, "connect_expired_btn"), callback_data="set:back")
        ]]
    )
    await bot.send_message(row.chat_id, t(lang, "connect_expired_notice"), reply_markup=kb)


async def _tick(bot: Bot, *, now: datetime | None = None) -> int:
    async with get_session() as db:
        rows = await connect_tokens.claim_expired_for_notice(db, now=now)
        await db.commit()

    for row in rows:
        try:
            await _notify(bot, row)
        except Exception:
            # Already claimed, so this one won't be retried — a failed
            # notification must not block the rest of the batch, and it's a
            # courtesy message, not something worth re-sending forever.
            logger.exception("connect_notices: failed to notify telegram_id=%s", row.telegram_id)
    return len(rows)


async def run_expiry_notices(bot: Bot, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
    """Long-running loop; cancel the task to stop it."""
    logger.info("connect_notices: sweeper started (every %ss)", interval_seconds)
    while True:
        try:
            sent = await _tick(bot)
            if sent:
                logger.info("connect_notices: notified %s expired attempt(s)", sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("connect_notices: sweep failed")
        await asyncio.sleep(interval_seconds)
