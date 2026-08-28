"""Delivery transport for the personal manager bot.

One shared Bot-API bot DMs each owner privately. The userbot workers (which
observe the events) push notifications through this; the manager_bot service
runs the /start side so owners can receive DMs.

Delivery is best-effort: if the owner never started the manager bot, Telegram
forbids the DM and `send_*` returns False instead of raising.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def _log_failure(kind: str, chat_id: int, exc: Exception) -> None:
    # "chat not found" simply means the owner hasn't started the manager bot yet
    logger.info("manager notify (%s) skipped for chat_id=%s: %s", kind, chat_id, exc)


class ManagerNotifier:
    def __init__(self, bot_token: str) -> None:
        self._bot = Bot(token=bot_token)

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await self._bot.send_message(
                chat_id, text, parse_mode=parse_mode, disable_web_page_preview=True, reply_markup=reply_markup
            )
            return True
        except TelegramAPIError as exc:
            _log_failure("text", chat_id, exc)
            return False

    async def send_photo(
        self,
        chat_id: int,
        photo: bytes,
        *,
        caption: str = "",
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await self._bot.send_photo(
                chat_id,
                BufferedInputFile(photo, filename="photo.jpg"),
                caption=caption or None,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except TelegramAPIError as exc:
            _log_failure("photo", chat_id, exc)
            return False

    async def send_voice(
        self,
        chat_id: int,
        voice: bytes,
        *,
        caption: str = "",
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await self._bot.send_voice(
                chat_id,
                BufferedInputFile(voice, filename="voice.ogg"),
                caption=caption or None,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except TelegramAPIError as exc:
            _log_failure("voice", chat_id, exc)
            return False

    async def send_video(
        self,
        chat_id: int,
        video: bytes,
        *,
        caption: str = "",
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await self._bot.send_video(
                chat_id,
                BufferedInputFile(video, filename="video.mp4"),
                caption=caption or None,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except TelegramAPIError as exc:
            _log_failure("video", chat_id, exc)
            return False

    async def send_document(
        self,
        chat_id: int,
        document: bytes,
        *,
        filename: str = "file",
        caption: str = "",
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await self._bot.send_document(
                chat_id,
                BufferedInputFile(document, filename=filename),
                caption=caption or None,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except TelegramAPIError as exc:
            _log_failure("document", chat_id, exc)
            return False

    async def send_location(
        self,
        chat_id: int,
        latitude: float,
        longitude: float,
        *,
        caption: str = "",
        parse_mode: str = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            # send_location has no caption param — send it as a separate
            # message right after, same as the location itself would read
            await self._bot.send_location(chat_id, latitude=latitude, longitude=longitude)
            if caption:
                await self._bot.send_message(chat_id, caption, parse_mode=parse_mode, reply_markup=reply_markup)
            return True
        except TelegramAPIError as exc:
            _log_failure("location", chat_id, exc)
            return False

    async def close(self) -> None:
        await self._bot.session.close()
