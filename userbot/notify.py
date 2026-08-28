"""Thin helper the userbot handlers use to DM the owner via the manager bot.

Returns False (never raises) when there's no notifier configured, no known
owner chat, or Telegram refuses the DM — callers can then fall back to acting
in-chat.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

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
) -> bool:
    if ctx.notifier is None or not ctx.owner_telegram_id:
        return False
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
