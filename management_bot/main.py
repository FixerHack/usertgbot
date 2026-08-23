"""Entrypoint for the public management bot.

Run independently: `uv run management-bot`
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from management_bot.config import settings
from management_bot.handlers import (
    admin,
    connect,
    help as help_handlers,
    menu,
    settings as settings_handlers,
    start,
    subscribe,
)
from shared.logging_conf import setup_logging

logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(subscribe.router)
    dp.include_router(settings_handlers.router)
    dp.include_router(connect.router)
    dp.include_router(help_handlers.router)
    dp.include_router(admin.router)
    dp.include_router(menu.router)
    return dp


async def run() -> None:
    setup_logging("management_bot")
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
