"""Manager bot's own interactive bits — currently just the "ignore this chat"
button attached to deleted/edited-message notifications (userbot/handlers/autosave.py)."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from db import queries
from db.session import get_session
from shared.i18n import lang_of, t

logger = logging.getLogger(__name__)
router = Router(name="manager_actions")


@router.callback_query(F.data.startswith("ignore_chat:"))
async def on_ignore_chat(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    chat_id = int(callback.data.split(":", 1)[1])
    async with get_session() as db:
        owner = await queries.get_user_by_telegram_id(db, callback.from_user.id)
        if owner is None:
            await callback.answer(t(lang, "ignore_user_error"), show_alert=True)
            return
        await queries.add_ignored_chat(db, owner.id, chat_id)
        await db.commit()

    await callback.answer(t(lang, "ignore_added"), show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("could not clear the ignore-chat button", exc_info=True)
