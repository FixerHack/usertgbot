"""/help and /support (also reachable from the reply-keyboard buttons)."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from management_bot.config import settings
from shared.i18n import lang_of, t

router = Router(name="help")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(t(lang_of(message), "help_text"))


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    lang = lang_of(message)
    await message.answer(t(lang, "support_text", contact=settings.support_contact))
