"""/connect flow: link the user's Telegram account as a userbot session.

Flow: /connect -> share phone (contact) -> Telethon send_code_request ->
enter code via inline number pad -> optional 2FA password -> sign_in ->
export session string -> Fernet-encrypt -> store in db.Session.

The live Telethon client is held by `login.login_manager`; aiogram FSM only
tracks which step the chat is on.
"""

import asyncio
import logging
from datetime import datetime, timezone
from math import ceil

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from connect_web.server import connect_server
from db import queries
from db.session import get_session
from management_bot import keyboards, storage
from management_bot.config import settings
from management_bot.login import (
    CodeTooShort,
    InvalidCode,
    LoginError,
    NoActiveLogin,
    NoMoreDeliveryOptions,
    WrongPassword,
    login_manager,
)
from shared import connect_tokens
from shared.i18n import lang_of, t, variants

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

    # An attempt already in flight: its Mini App button is still in the chat
    # and still valid. Handing out a second token here would silently orphan
    # that button (it would still open, but onto a login the user has moved
    # on from), so point them back at the one they already have instead.
    if settings.webapp_api_id and settings.webapp_api_hash:
        async with get_session() as db:
            pending = await connect_tokens.get_active_token(db, message.chat.id)
        if pending is not None:
            left = pending.expires_at - datetime.now(timezone.utc).replace(tzinfo=None)
            minutes = max(1, ceil(left.total_seconds() / 60))
            await state.clear()
            await message.answer(t(lang, "connect_already_active", minutes=minutes))
            return

    # A re-link after unlinking (or after a session died) already has a phone
    # on file from a previous connect — skip asking for the contact button
    # again. Bot API can't read a phone from the profile even if the user has
    # it set to "everyone" visible, so a past session is the only source that
    # actually exists.
    async with get_session() as db:
        known_phone = await storage.get_last_known_phone(db, message.chat.id)
    if known_phone:
        await _dispatch_phone(message, state, known_phone)
        return

    await state.set_state(ConnectStates.waiting_for_phone)
    await message.answer(t(lang, "connect_ask_phone"), reply_markup=keyboards.phone_request_keyboard(lang))


@router.message(ConnectStates.waiting_for_phone, F.contact)
async def on_contact(message: Message, state: FSMContext) -> None:
    phone = storage.normalize_phone(message.contact.phone_number)
    await _dispatch_phone(message, state, phone)


@router.message(ConnectStates.waiting_for_phone, F.text)
async def on_typed_phone(message: Message, state: FSMContext) -> None:
    phone = storage.normalize_phone(message.text)
    if len(phone) < 8:  # "+" plus at least a few digits
        await message.answer(t(lang_of(message), "connect_bad_phone"), reply_markup=keyboards.phone_request_keyboard(lang_of(message)))
        return
    await _dispatch_phone(message, state, phone)


async def _dispatch_phone(message: Message, state: FSMContext, phone: str) -> None:
    """Both entry points converge here. Unset WEBAPP_API_ID/HASH (the default,
    including on production right now) falls straight through to the old
    in-chat keypad flow, untouched — this is the whole switch, no explicit
    feature flag needed."""
    if settings.webapp_api_id and settings.webapp_api_hash:
        await _begin_miniapp_login(message, state, phone)
    else:
        await _begin_login(message, state, phone)


async def _begin_miniapp_login(message: Message, state: FSMContext, phone: str) -> None:
    lang = lang_of(message)
    await state.clear()  # the rest of the flow happens in the Mini App, not the chat

    async with get_session() as db:
        try:
            token = await connect_tokens.create_token(
                db, telegram_id=message.chat.id, chat_id=message.chat.id, phone=phone
            )
        except ValueError:
            await db.rollback()
            await message.answer(t(lang, "connect_rate_limited"))
            return
        await db.commit()
        token_value, token_id = token.token, token.id

    try:
        base_url = await connect_server.start(
            port=settings.connect_web_port,
            bot_token=settings.bot_token,
            webapp_api_id=settings.webapp_api_id,
            webapp_api_hash=settings.webapp_api_hash,
            ngrok_authtoken=settings.ngrok_authtoken,
            ngrok_domain=settings.ngrok_domain,
            manager_bot_username=settings.manager_bot_username,
        )
    except Exception:
        logger.exception("failed to start connect_web for chat_id=%s", message.chat.id)
        await message.answer(t(lang, "connect_error"))
        return

    if not base_url.startswith("https://"):
        # Telegram refuses to open a web_app button over plain http — sending
        # it anyway would be a dead button with no error visible to the user.
        # Most likely cause locally: NGROK_AUTHTOKEN isn't set.
        logger.error(
            "connect_web has no HTTPS origin (got %s) — check NGROK_AUTHTOKEN; refusing to send a dead button",
            base_url,
        )
        await message.answer(t(lang, "connect_error"))
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=t(lang, "connect_miniapp_button"),
                web_app=WebAppInfo(url=f"{base_url}/connect/{token_value}"),
            )
        ]]
    )
    sent = await message.answer(t(lang, "connect_miniapp_intro"), reply_markup=kb)

    async with get_session() as db:
        await connect_tokens.set_message_id(db, token_id, sent.message_id)
        await db.commit()


# Fake-but-smooth progress: there's no real byte-level progress to report
# for a single MTProto round trip, so this animates a decelerating percentage
# that never reaches 100% on its own — it holds near the top until the real
# result lands, then the caller replaces the text outright.
_PROGRESS_STEPS = (8, 20, 35, 50, 64, 76, 86, 93)
_PROGRESS_INTERVAL = 1.2  # seconds between edits — well under Telegram's edit rate limit
_PROGRESS_BAR_WIDTH = 10


def _progress_bar(pct: int) -> str:
    filled = round(_PROGRESS_BAR_WIDTH * pct / 100)
    return "▓" * filled + "░" * (_PROGRESS_BAR_WIDTH - filled) + f" {pct}%"


async def _with_progress(message: Message, label: str, coro):
    task = asyncio.ensure_future(coro)
    for pct in _PROGRESS_STEPS:
        if task.done():
            break
        try:
            await message.edit_text(f"{label}\n{_progress_bar(pct)}")
        except Exception:
            logger.debug("progress edit skipped", exc_info=True)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_PROGRESS_INTERVAL)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break
    return await task


async def _begin_login(message: Message, state: FSMContext, phone: str) -> None:
    lang = lang_of(message)
    # instant feedback: client.connect() + send_code_request is a real network
    # round trip to Telegram (occasionally involving a DC handoff) and can take
    # a few seconds, so show something before that gap rather than after it.
    placeholder = await message.answer(t(lang, "connect_working"))
    try:
        await _with_progress(placeholder, t(lang, "connect_working"), login_manager.start(message.from_user.id, phone))
    except Exception:
        logger.exception("failed to start login for user_id=%s", message.from_user.id)
        await state.clear()
        await placeholder.edit_text(t(lang, "connect_error"))
        return
    await state.set_state(ConnectStates.waiting_for_code)
    await placeholder.edit_text(t(lang, "code_sent"), reply_markup=keyboards.code_keyboard(lang))


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

    if data == keyboards.CB_RESEND_SMS:
        await _resend_code(callback, state)
        return

    if data.startswith(keyboards.CB_DIGIT):
        login_manager.push_digit(user_id, data.removeprefix(keyboards.CB_DIGIT))
    elif data == keyboards.CB_BACKSPACE:
        login_manager.backspace(user_id)

    await _render_code(callback, login_manager.current_code(user_id))
    await callback.answer()


# Telegram hard-rejects (MESSAGE_TOO_LONG) any callback-query alert text over
# 200 chars — confirmed live: an untranslated-length i18n string crashed the
# whole update with an unhandled exception instead of showing anything.
# Truncating here is a safety net so a future translation edit can never
# repeat that, regardless of how long the given key's text happens to be.
async def _alert(callback: CallbackQuery, text: str) -> None:
    await callback.answer(text[:200], show_alert=True)


async def _resend_code(callback: CallbackQuery, state: FSMContext) -> None:
    lang = lang_of(callback)
    user_id = callback.from_user.id
    try:
        await login_manager.resend_code(user_id)
    except NoActiveLogin:
        await state.clear()
        await callback.message.edit_text(t(lang, "connect_error"))
        await callback.answer()
        return
    except NoMoreDeliveryOptions:
        await _alert(callback, t(lang, "sms_no_more_options"))
        return
    except Exception:
        logger.exception("resend_code failed for user_id=%s", user_id)
        await _alert(callback, t(lang, "sms_resend_error"))
        return
    await _alert(callback, t(lang, "sms_resent"))
    await _render_code(callback, login_manager.current_code(user_id))


_MENU_BUTTON_TEXTS = {
    text
    for key in ("btn_subscribe", "btn_settings", "btn_help", "btn_support", "btn_menu")
    for text in variants(key)
}


@router.message(ConnectStates.waiting_for_code, F.text, ~F.text.startswith("/"), ~F.text.in_(_MENU_BUTTON_TEXTS))
async def on_typed_code_warning(message: Message) -> None:
    """DO NOT process this as the code. Typing the login code as a plain
    Telegram message — even into our own bot's chat — can trip Telegram's
    own anti-fraud system and invalidate that code; this is exactly why the
    inline number pad exists (see login.py's module docstring). This
    handler exists ONLY to explain that to a confused user instead of
    silently ignoring their message (the previous, correct-but-confusing
    behavior — a real customer typed their code here and got no feedback
    at all).

    Explicitly excludes reply-keyboard menu buttons and slash commands: a
    user stuck in this state WILL tap other menu buttons to escape, and an
    earlier version of this handler matched bare `F.text` — swallowing
    every one of those taps behind this same warning with no way out
    (confirmed live: a stuck user got nothing but this warning for every
    single menu button they pressed)."""
    await message.answer(t(lang_of(message), "code_must_use_keyboard"))


async def _submit_code(callback: CallbackQuery, state: FSMContext) -> None:
    lang = lang_of(callback)
    user_id = callback.from_user.id
    # only animate progress once the code is actually full — a short code
    # fails instantly (no network call), so don't flash the bar there
    ready = len(login_manager.current_code(user_id)) >= keyboards.CODE_LENGTH
    try:
        if ready:
            result = await _with_progress(
                callback.message, t(lang, "connect_checking_code"), login_manager.submit_code(user_id)
            )
        else:
            result = await login_manager.submit_code(user_id)
    except CodeTooShort:
        await _alert(callback, t(lang, "code_too_short"))
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
    # the password message is gone, so a fresh placeholder carries the wait —
    # 2FA sign-in does real SRP crypto + a network round trip, not instant
    placeholder = await message.answer(t(lang, "connect_checking_password"))
    try:
        result = await _with_progress(
            placeholder, t(lang, "connect_checking_password"), login_manager.submit_password(message.from_user.id, password)
        )
    except WrongPassword:
        await placeholder.edit_text(t(lang, "wrong_password"))
        return
    except (LoginError, NoActiveLogin):
        await state.clear()
        await placeholder.edit_text(t(lang, "connect_error"))
        return
    await _persist_and_finish(placeholder, state, result, lang)


async def _finish_reply(message: Message, text: str, **kwargs) -> None:
    """`message` here is always a bot-sent placeholder (see callers), so
    editing it in place works — no message-spam in the connect flow."""
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


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
        await _finish_reply(message, t(lang, "connect_error"))
        return

    await state.clear()
    await _finish_reply(message, t(lang, "connect_success"), reply_markup=keyboards.main_menu(lang))

    # A separate message with its own button — the manager bot is where all
    # notifications actually arrive, and buried as a trailing line in the
    # success text it was easy to miss (users went looking for where
    # notifications were, not realizing this step was required).
    if settings.manager_bot_username:
        manager = f"@{settings.manager_bot_username}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                text=t(lang, "start_manager_btn"), url=f"https://t.me/{settings.manager_bot_username}"
            )]]
        )
        await message.answer(t(lang, "start_manager_hint", manager=manager), reply_markup=kb)
    else:
        await message.answer(t(lang, "start_manager_hint", manager="менеджер-бот"))


async def _render_code(callback: CallbackQuery, code: str) -> None:
    await _render_code_text(callback, t(lang_of(callback), "code_label", masked=keyboards.masked_code(code)))


async def _render_code_text(callback: CallbackQuery, text: str) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=keyboards.code_keyboard(lang_of(callback)))
    except Exception:
        # Most likely "message is not modified" — safe to ignore.
        logger.debug("code render edit skipped", exc_info=True)
