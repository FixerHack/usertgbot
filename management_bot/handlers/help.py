"""/help and /support (also reachable from the reply-keyboard buttons).

Help is plan-aware: someone on Pro shouldn't have to read past the Standard
limits to find their own, and someone with no subscription has nothing
plan-specific to show, so they get the full picture straight away. A single
inline button toggles between "what I have" and "everything there is" — the
two views replace each other in place rather than piling up messages.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db import queries
from db.session import get_session
from management_bot.config import settings
from shared.i18n import lang_of, t
from shared.tariffs import Tariff

router = Router(name="help")

CB_HELP_ALL = "help:all"
CB_HELP_MINE = "help:mine"

_PLAN_VIEWS = {
    Tariff.STANDARD: "help_standard",
    Tariff.PRO: "help_pro",
    Tariff.PREMIUM: "help_premium",
}


def _render(lang: str, key: str) -> str:
    """Fill a view's shared sections. Kept in one place so the bot/manager/
    autosave wording is identical everywhere it appears."""
    return t(lang, key).format(
        bot=t(lang, "help_bot_section"),
        manager=t(lang, "help_manager_section"),
        how=t(lang, "help_how_commands"),
        autosave=t(lang, "help_autosave_section"),
    )


def _toggle_keyboard(lang: str, *, showing_all: bool) -> InlineKeyboardMarkup:
    label, data = (
        (t(lang, "help_btn_mine"), CB_HELP_MINE)
        if showing_all
        else (t(lang, "help_btn_all"), CB_HELP_ALL)
    )
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data)]])


async def _plan_of(telegram_id: int) -> Tariff | None:
    async with get_session() as db:
        sub = await queries.get_active_subscription_by_telegram_id(db, telegram_id)
    if sub is None:
        return None
    try:
        return Tariff(sub.tariff)
    except ValueError:  # a tariff string the enum no longer knows
        return None


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    lang = lang_of(message)
    plan = await _plan_of(message.chat.id)
    if plan is None:
        # Nothing plan-specific to show, and no second view to toggle to.
        await message.answer(_render(lang, "help_all"), parse_mode="HTML")
        return
    await message.answer(
        _render(lang, _PLAN_VIEWS[plan]),
        parse_mode="HTML",
        reply_markup=_toggle_keyboard(lang, showing_all=False),
    )


@router.callback_query(F.data == CB_HELP_ALL)
async def on_show_all(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    await callback.message.edit_text(
        _render(lang, "help_all"),
        parse_mode="HTML",
        reply_markup=_toggle_keyboard(lang, showing_all=True),
    )
    await callback.answer()


@router.callback_query(F.data == CB_HELP_MINE)
async def on_show_mine(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    plan = await _plan_of(callback.message.chat.id)
    if plan is None:
        # Subscription lapsed while the message sat open — there's no "mine"
        # to go back to, so drop the toggle rather than show an empty view.
        await callback.message.edit_text(_render(lang, "help_all"), parse_mode="HTML")
        await callback.answer()
        return
    await callback.message.edit_text(
        _render(lang, _PLAN_VIEWS[plan]),
        parse_mode="HTML",
        reply_markup=_toggle_keyboard(lang, showing_all=False),
    )
    await callback.answer()


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    lang = lang_of(message)
    await message.answer(t(lang, "support_text", contact=settings.support_contact))
