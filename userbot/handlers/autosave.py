"""Auto-save messages that others delete or edit (Standard tier).

Incoming messages are cached in memory; when a delete/edit event fires we
resolve the original content from the cache and persist it — but only if the
owner currently has an active subscription.
"""

from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from telethon import TelegramClient, events

from db.queries import get_active_subscription_for_user, is_chat_ignored, save_captured_message
from db.session import get_session
from shared.i18n import t
from userbot import entities, formatting
from userbot.context import WorkerContext
from userbot.notify import notify_owner

logger = logging.getLogger(__name__)


def _ignore_chat_kb(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t(lang, "btn_ignore_chat"), callback_data=f"ignore_chat:{chat_id}")]]
    )


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(incoming=True))
    async def remember(event: events.NewMessage.Event) -> None:
        ctx.cache.remember(
            chat_id=event.chat_id,
            message_id=event.id,
            sender_id=event.sender_id,
            text=event.raw_text,
        )

    @client.on(events.MessageDeleted())
    async def on_deleted(event: events.MessageDeleted.Event) -> None:
        for message_id in event.deleted_ids:
            # For DMs/small groups Telegram's delete update carries no peer at
            # all, so event.chat_id is None here — fall back to the reverse
            # index (see message_cache.py) instead of silently dropping it.
            cached = (
                ctx.cache.pop(event.chat_id, message_id)
                if event.chat_id
                else ctx.cache.pop_by_message_id(message_id)
            )
            if cached is None or not cached.text:
                continue
            await _handle(client, ctx, cached.chat_id, message_id, cached.sender_id, "deleted", cached.text, None)

    @client.on(events.MessageEdited(incoming=True))
    async def on_edited(event: events.MessageEdited.Event) -> None:
        cached = ctx.cache.get(event.chat_id, event.id)
        previous = cached.text if cached else None
        ctx.cache.remember(event.chat_id, event.id, event.sender_id, event.raw_text)
        if previous is None or previous == event.raw_text:
            return
        await _handle(client, ctx, event.chat_id, event.id, event.sender_id, "edited", event.raw_text, previous)


async def _handle(client, ctx, chat_id, message_id, sender_id, event_type, text, previous_text) -> None:
    # never react to bots — including our own management/manager bots
    sender_ref, is_bot = (await entities.resolve(client, sender_id)) if sender_id else ("невідомо", False)
    if is_bot:
        return
    chat_ref = await entities.ref(client, chat_id)

    try:
        async with get_session() as db:
            if await get_active_subscription_for_user(db, ctx.owner_user_id) is None:
                return
            if await is_chat_ignored(db, ctx.owner_user_id, chat_id):
                return
            await save_captured_message(
                db,
                owner_user_id=ctx.owner_user_id,
                chat_id=chat_id,
                message_id=message_id,
                sender_id=sender_id,
                event_type=event_type,
                text=text,
                previous_text=previous_text,
            )
            await db.commit()
    except Exception:
        logger.exception("failed to auto-save %s message", event_type)
        return

    if event_type == "deleted":
        notice = formatting.format_deleted_notice(chat_ref, sender_ref, text, ctx.owner_lang)
    else:
        notice = formatting.format_edited_notice(chat_ref, sender_ref, previous_text or "", text or "", ctx.owner_lang)
    await notify_owner(ctx, notice, reply_markup=_ignore_chat_kb(chat_id, ctx.owner_lang))
