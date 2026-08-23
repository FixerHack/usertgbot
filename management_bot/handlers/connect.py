"""/connect flow: link the user's Telegram account as a userbot session.

Flow: /connect -> share phone (contact) -> Telethon send_code_request ->
enter code via inline number pad -> optional 2FA password -> sign_in ->
export session string -> Fernet-encrypt -> store in db.Session.

The live Telethon client is held by `login.login_manager`; aiogram FSM only
tracks which step the chat is on.
"""

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from db import queries
from db.session import get_session
from management_bot import keyboards, storage
from management_bot.config import settings
from management_bot.login import (
    CodeTooShort,
    InvalidCode,
    LoginError,
    NoActiveLogin,
    WrongPassword,
    login_manager,
)
from shared.i18n import lang_of, t

logger = logging.getLogger(__name__)
router = Router(name="connect")


class ConnectStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()


@router.message(Command("connect"))
async def cmd_connect(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    async with get_session() as db:
        active = await queries.get_active_subscription_by_telegram_id(db, message.chat.id)
    if active is None:
        await message.answer(t(lang, "need_subscription"))
        return
    await state.set_state(ConnectStates.waiting_for_phone)
    await message.answer(t(lang, "connect_ask_phone"), reply_markup=keyboards.phone_request_keyboard(lang))


@router.message(ConnectStates.waiting_for_phone, F.contact)
async def on_contact(message: Message, state: FSMContext) -> None:
    phone = storage.normalize_phone(message.contact.phone_number)
    await _begin_login(message, state, phone)


@router.message(ConnectStates.waiting_for_phone, F.text)
async def on_typed_phone(message: Message, state: FSMContext) -> None:
    phone = storage.normalize_phone(message.text)
    if len(phone) < 8:  # "+" plus at least a few digits
        await message.answer(t(lang_of(message), "connect_bad_phone"), reply_markup=keyboards.phone_request_keyboard(lang_of(message)))
        return
    await _begin_login(message, state, phone)


async def _begin_login(message: Message, state: FSMContext, phone: str) -> None:
    lang = lang_of(message)
    try:
        await login_manager.start(message.from_user.id, phone)
    except Exception:
        logger.exception("failed to start login for user_id=%s", message.from_user.id)
        await state.clear()
        await message.answer(t(lang, "connect_error"))
        return
    await state.set_state(ConnectStates.waiting_for_code)
    await message.answer(t(lang, "code_sent"), reply_markup=keyboards.code_keyboard())


@router.callback_query(ConnectStates.waiting_for_code, F.data.startswith("cc:"))
async def on_code_button(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    if not login_manager.has_login(user_id):
        await state.clear()
        await callback.answer()
        return

    data = callback.data
    if data == keyboards.CB_SUBMIT:
        await _submit_code(callback, state)
        return

    if data.startswith(keyboards.CB_DIGIT):
        login_manager.push_digit(user_id, data.removeprefix(keyboards.CB_DIGIT))
    elif data == keyboards.CB_BACKSPACE:
        login_manager.backspace(user_id)

    await _render_code(callback, login_manager.current_code(user_id))
    await callback.answer()


async def _submit_code(callback: CallbackQuery, state: FSMContext) -> None:
    lang = lang_of(callback)
    user_id = callback.from_user.id
    try:
        result = await login_manager.submit_code(user_id)
    except CodeTooShort:
        await callback.answer(t(lang, "code_too_short"), show_alert=True)
        return
    except InvalidCode:
        await _render_code_text(callback, t(lang, "invalid_code"))
        await callback.answer()
        return
    except (LoginError, NoActiveLogin):
        await state.clear()
        await callback.message.edit_text(t(lang, "connect_error"))
        await callback.answer()
        return

    if result.status == "needs_password":
        await state.set_state(ConnectStates.waiting_for_password)
        await callback.message.edit_text(t(lang, "enter_2fa"))
        await callback.answer()
        return

    await _persist_and_finish(callback.message, state, result, lang)
    await callback.answer()


@router.message(ConnectStates.waiting_for_password, F.text)
async def on_password(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    password = message.text
    # remove the 2FA password from the chat immediately (security)
    try:
        await message.delete()
    except Exception:
        logger.debug("could not delete password message", exc_info=True)
    try:
        result = await login_manager.submit_password(message.from_user.id, password)
    except WrongPassword:
        await message.answer(t(lang, "wrong_password"))
        return
    except (LoginError, NoActiveLogin):
        await state.clear()
        await message.answer(t(lang, "connect_error"))
        return
    await _persist_and_finish(message, state, result, lang)


async def _persist_and_finish(message: Message, state: FSMContext, result, lang: str) -> None:
    account = result.account
    full_name = account.display_name if account else None
    try:
        async with get_session() as db:
            await storage.save_session(
                db,
                telegram_id=message.chat.id,
                phone_number=result.phone,
                session_string=result.session_string,
                username=account.username if account else None,
                full_name=full_name,
            )
            await db.commit()
    except Exception:
        logger.exception("failed to persist session for chat_id=%s", message.chat.id)
        await state.clear()
        # answer (not edit): on the 2FA path `message` is the user's password msg
        await message.answer(t(lang, "connect_error"))
        return

    await state.clear()
    text = t(
        lang, "connect_success",
        name=html.escape(full_name) if full_name else "—",
        user_id=account.user_id if account else "—",
        phone=result.phone,
    )
    manager = f"@{settings.manager_bot_username}" if settings.manager_bot_username else "менеджер-бот"
    text += t(lang, "start_manager_hint", manager=manager)
    await message.answer(text, reply_markup=keyboards.main_menu(lang))


async def _render_code(callback: CallbackQuery, code: str) -> None:
    await _render_code_text(callback, t(lang_of(callback), "code_label", masked=keyboards.masked_code(code)))


async def _render_code_text(callback: CallbackQuery, text: str) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=keyboards.code_keyboard())
    except Exception:
        # Most likely "message is not modified" — safe to ignore.
        logger.debug("code render edit skipped", exc_info=True)
