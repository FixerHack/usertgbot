"""Entrypoint for the public management bot.

Run independently: `uv run management-bot`
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from connect_web.server import connect_server
from management_bot.config import settings
from management_bot.connect_notices import run_expiry_notices
from management_bot.handlers import (
    admin,
    connect,
    help as help_handlers,
    menu,
    settings as settings_handlers,
    start,
    subscribe,
)
from management_bot.middlewares import LangAndBlockMiddleware, WatchConnectMiddleware
from shared.logging_conf import setup_logging
from shared.ratelimit import RateLimitMiddleware

logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    # outermost — sees every raw update for the watch-list before any
    # type-specific routing/middleware even runs. TEMPORARY, see its docstring.
    dp.update.outer_middleware(WatchConnectMiddleware())
    # rate limit first — a flood never reaches the DB-backed lang/block check
    dp.message.outer_middleware(RateLimitMiddleware())
    dp.callback_query.outer_middleware(RateLimitMiddleware())
    dp.message.outer_middleware(LangAndBlockMiddleware())
    dp.callback_query.outer_middleware(LangAndBlockMiddleware())
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
    # Only meaningful when the Mini App flow is on; with the old in-chat
    # keypad flow no connect tokens are ever issued, so the sweep would just
    # poll an empty table forever.
    sweeper = None
    if settings.webapp_api_id and settings.webapp_api_hash:
        sweeper = asyncio.create_task(run_expiry_notices(bot))
        # Started eagerly here, not lazily on the first "Прив'язати акаунт"
        # tap. A token (and its chat button) can outlive a restart just fine —
        # it's a DB row with a TTL — but the HTTP server behind that button
        # cannot start itself. Starting it lazily meant that if a restart
        # landed while a still-valid token existed, the "already connecting"
        # guard would block the very handler that used to (re)start this
        # server, leaving the button pointing at nothing: confirmed live as
        # ERR_NGROK_3200 on a token the DB still considered perfectly active.
        try:
            await connect_server.start(
                port=settings.connect_web_port,
                bot_token=settings.bot_token,
                webapp_api_id=settings.webapp_api_id,
                webapp_api_hash=settings.webapp_api_hash,
                ngrok_authtoken=settings.ngrok_authtoken,
                ngrok_domain=settings.ngrok_domain,
                manager_bot_username=settings.manager_bot_username,
                public_url=settings.connect_public_url,
                host=settings.connect_web_host,
            )
        except Exception:
            logger.exception("connect_web failed to start at boot; will retry on first connect attempt")
    try:
        await dp.start_polling(bot)
    finally:
        if sweeper is not None:
            sweeper.cancel()
        await connect_server.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
