"""Entrypoint for the personal manager bot.

Run independently: `uv run manager-bot`

Its only interactive job is /start: once an owner starts it, Telegram allows
the bot to DM them, and the userbot workers can push notifications through
shared.notify. All the actual notifications are sent by the userbot side.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from db import queries
from db.session import get_session
from manager_bot import handlers as action_handlers
from manager_bot.config import settings
from shared.i18n import lang_of, t
from shared.logging_conf import setup_logging

logger = logging.getLogger(__name__)

router = Router(name="manager")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    lang = lang_of(message)
    # keep the owner's language fresh (userbot reads it for notifications)
    if message.from_user is not None:
        async with get_session() as db:
            await queries.set_user_language(db, message.chat.id, message.from_user.language_code)
            await db.commit()
    await message.answer(t(lang, "manager_greeting"))


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(action_handlers.router)
    return dp


async def run() -> None:
    setup_logging("manager_bot")
    bot = Bot(token=settings.manager_bot_token)
    dp = build_dispatcher()
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
