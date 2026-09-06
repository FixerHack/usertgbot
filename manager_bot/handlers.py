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


@router.callback_query(F.data == "notice:del")
async def on_delete_notice(callback: CallbackQuery) -> None:
    """Throw away one recovered message.

    Only ever deletes the message the button is attached to — a message this
    bot sent, in a private chat with its owner — so there is nothing to
    authorise beyond that. Telegram refuses to delete anything older than 48
    hours, and there is no useful thing to say about that: the button is
    simply cleared so it stops looking live.
    """
    try:
        await callback.message.delete()
        await callback.answer()
        return
    except Exception:
        logger.debug("could not delete a notice", exc_info=True)

    await callback.answer(t(lang_of(callback), "notice_delete_failed"), show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("could not clear the delete button either", exc_info=True)


@router.callback_query(F.data.startswith("ignore_chat:"))
async def on_ignore_chat(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    _, chat_id_str, *title_parts = callback.data.split(":")
    chat_id = int(chat_id_str)
    chat_title = ":".join(title_parts).strip() or None
    async with get_session() as db:
        owner = await queries.get_user_by_telegram_id(db, callback.from_user.id)
        if owner is None:
            await callback.answer(t(lang, "ignore_user_error"), show_alert=True)
            return
        await queries.add_ignored_chat(db, owner.id, chat_id, chat_title=chat_title)
        await db.commit()

    await callback.answer(t(lang, "ignore_added"), show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.debug("could not clear the ignore-chat button", exc_info=True)
