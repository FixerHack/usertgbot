"""Thin helper the userbot handlers use to DM the owner via the manager bot.

Returns None (never raises) when there's no notifier configured, no known
owner chat, or Telegram refuses the DM — callers can then fall back to acting
in-chat. On success it hands back the sent message, which is what lets one
notice refer to another.
"""

from __future__ import annotations

import asyncio

from aiogram.types import InlineKeyboardMarkup, Message

from userbot.context import WorkerContext


async def notify_owner(
    ctx: WorkerContext,
    text: str,
    *,
    photo: bytes | None = None,
    voice: bytes | None = None,
    video: bytes | None = None,
    document: bytes | None = None,
    document_filename: str = "file",
    location: tuple[float, float] | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    if ctx.notifier is None or not ctx.owner_telegram_id:
        return None
    chat_id = ctx.owner_telegram_id
    if voice is not None:
        return await ctx.notifier.send_voice(chat_id, voice, caption=text, reply_markup=reply_markup)
    if photo is not None:
        return await ctx.notifier.send_photo(chat_id, photo, caption=text, reply_markup=reply_markup)
    if video is not None:
        return await ctx.notifier.send_video(chat_id, video, caption=text, reply_markup=reply_markup)
    if document is not None:
        return await ctx.notifier.send_document(
            chat_id, document, filename=document_filename, caption=text, reply_markup=reply_markup
        )
    if location is not None:
        lat, lon = location
        return await ctx.notifier.send_location(chat_id, lat, lon, caption=text, reply_markup=reply_markup)
    return await ctx.notifier.send_text(chat_id, text, reply_markup=reply_markup)


async def notify_owner_briefly(ctx: WorkerContext, text: str, *, seconds: float = 8.0) -> bool:
    """Tell the owner something in their own chat with the bot, then clear it.

    For feedback that belongs to the owner and nobody else — the reply to
    `.save`, say, which must not be posted into the chat being recorded. The
    deletion runs as its own task so the caller is not held for `seconds`,
    and a worker that shuts down first simply leaves the notice behind, which
    is the harmless end of that trade.
    """
    sent = await notify_owner(ctx, text)
    if sent is None:
        return False

    async def _clear() -> None:
        await asyncio.sleep(seconds)
        await ctx.notifier.delete_message(ctx.owner_telegram_id, sent.message_id)

    asyncio.create_task(_clear())
    return True
