from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from db.session import get_session
from management_bot import dashboard, keyboards, storage
from management_bot.handlers import connect
from shared import referrals
from shared.i18n import lang_of

router = Router(name="start")

_REF_PREFIX = "ref_"
# Deep link used by the "re-link account" button on the session-expired notice
# the userbot sends (userbot/worker.py) — drops the owner straight into the
# connect flow instead of the dashboard.
_CONNECT_PAYLOAD = "connect"


async def show_dashboard(message: Message, *, referral_code: str | None = None) -> None:
    """The actual /start body — upsert the user, render the dashboard. Split
    out so the "🏠 Меню" reply-button (management_bot/handlers/menu.py) can
    call it directly without needing a CommandObject/FSMContext, which only
    the real /start command has."""
    lang = lang_of(message)
    full_name = message.from_user.full_name if message.from_user else None
    username = message.from_user.username if message.from_user else None
    language_code = message.from_user.language_code if message.from_user else None
    async with get_session() as db:
        user = await storage.upsert_user(
            db, message.chat.id, username=username, full_name=full_name, language_code=language_code
        )
        if referral_code:
            await referrals.attribute_user(db, user, referral_code)
        await db.commit()
        status = await storage.get_user_status(db, message.chat.id)

    text = dashboard.build_start_text(status, datetime.now(timezone.utc), lang)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboards.main_menu(lang))


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    referral_code = command.args[len(_REF_PREFIX):] if command.args and command.args.startswith(_REF_PREFIX) else None
    await show_dashboard(message, referral_code=referral_code)

    if command.args == _CONNECT_PAYLOAD:
        await connect.cmd_connect(message, state)
