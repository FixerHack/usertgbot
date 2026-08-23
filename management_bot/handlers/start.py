from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from db.session import get_session
from management_bot import dashboard, keyboards, storage
from shared.i18n import lang_of

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    lang = lang_of(message)
    full_name = message.from_user.full_name if message.from_user else None
    username = message.from_user.username if message.from_user else None
    language_code = message.from_user.language_code if message.from_user else None
    async with get_session() as db:
        await storage.upsert_user(
            db, message.chat.id, username=username, full_name=full_name, language_code=language_code
        )
        await db.commit()
        status = await storage.get_user_status(db, message.chat.id)

    text = dashboard.build_start_text(status, datetime.now(timezone.utc), lang)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboards.main_menu(lang))
