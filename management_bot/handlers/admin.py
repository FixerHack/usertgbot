"""Admin-only commands to launch/stop the web panel (via ngrok)."""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from admin_web.server import admin_server
from management_bot.config import settings
from shared.i18n import lang_of, t

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(message: Message) -> bool:
    return bool(message.from_user) and message.from_user.id in settings.admin_id_set


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message):
        return  # silent for non-admins
    lang = lang_of(message)
    await message.answer(t(lang, "adm_starting"))
    try:
        me = await message.bot.get_me()
        url, token = await admin_server.start(
            port=settings.admin_panel_port, ngrok_authtoken=settings.ngrok_authtoken, bot_username=me.username
        )
    except Exception:
        logger.exception("failed to start admin panel")
        await message.answer(t(lang, "adm_start_failed"))
        return
    await message.answer(t(lang, "adm_panel_ready", url=url, token=token))


@router.message(Command("admin_stop"))
async def cmd_admin_stop(message: Message) -> None:
    if not _is_admin(message):
        return
    await admin_server.stop()
    await message.answer(t(lang_of(message), "adm_stopped"))
