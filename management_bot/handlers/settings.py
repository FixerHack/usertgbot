"""/settings: profile + configure the `.me` card (Standard), autoresponder
(Pro), and the ignored-chats list. Values live in db.UserSettings (JSON) /
db.MediaBlob / db.IgnoredChat; the userbot reads them at runtime."""

import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    ReplyKeyboardMarkup,
)

from db import queries
from db.session import get_session
from management_bot import keyboards, storage
from management_bot.handlers import connect
from management_bot.payment import build_wayforpay
from management_bot.storage import upsert_user
from shared.i18n import lang_of, t, variants
from shared.settings_schema import (
    Autoresponder,
    Features,
    MeCard,
    is_valid_url,
    parse_buttons,
    parse_hhmm,
)
from shared.tariffs import Tariff, get_plan, tariff_grants_command
from shared.transcript import build_archive

# Telegram echoes this back with the picked user, so it only has to be stable
# within one bot — it distinguishes our picker from any other we might add.
_RECORD_REQUEST_ID = 1

logger = logging.getLogger(__name__)
router = Router(name="settings")


class SettingsStates(StatesGroup):
    me_text = State()
    me_link = State()
    me_photo = State()
    me_buttons = State()
    ar_message = State()
    ar_photo = State()
    ar_buttons = State()
    ar_time = State()
    ar_exceptions = State()


# --- menus -----------------------------------------------------------------


def _main_menu(
    has_account: bool, lang: str, *, auto_renew: bool = False, can_record: bool = False
) -> InlineKeyboardMarkup:
    account_btn = (
        InlineKeyboardButton(text=t(lang, "set_btn_unlink"), callback_data="set:unlink")
        if has_account
        else InlineKeyboardButton(text=t(lang, "set_btn_link"), callback_data="set:link")
    )
    # Three short labels share the top row; everything below gets a row of its
    # own so the longer names are not truncated into "Автовідповід…".
    rows = [
        [
            InlineKeyboardButton(text=t(lang, "set_btn_me"), callback_data="set:me"),
            InlineKeyboardButton(text=t(lang, "set_btn_features"), callback_data="set:features"),
            InlineKeyboardButton(text=t(lang, "set_btn_lang"), callback_data="set:lang"),
        ],
        [InlineKeyboardButton(text=t(lang, "set_btn_ar"), callback_data="set:ar")],
        [InlineKeyboardButton(text=t(lang, "set_btn_ignored"), callback_data="set:ignored")],
    ]
    if can_record:
        # Only on a plan that includes it — a button that answers
        # "your tariff does not include this" is a worse door than no door.
        rows.append([InlineKeyboardButton(text=t(lang, "rec_btn_open"), callback_data="set:rec")])
    rows += [
        [account_btn],
        # Every submenu has a way back to settings; settings itself had none,
        # so the only way out was the reply keyboard below the input box.
        [InlineKeyboardButton(text=t(lang, "btn_menu"), callback_data="set:menu")],
    ]
    if auto_renew:
        # Only shown while there is something to cancel — a standing card
        # mandate is exactly the thing a user must be able to stop without
        # writing to support.
        rows.append(
            [InlineKeyboardButton(text=t(lang, "set_autorenew_btn_cancel"), callback_data="set:autorenew:cancel")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tariff_title(raw: str) -> str:
    try:
        return get_plan(Tariff(raw)).title
    except (ValueError, KeyError):
        return raw


async def _build_settings(db, telegram_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    user = await queries.get_user_by_telegram_id(db, telegram_id)
    status = await storage.get_user_status(db, telegram_id)
    active = await queries.get_active_subscription_by_telegram_id(db, telegram_id)

    has_account = bool(status.sessions)
    joined = f"{user.created_at:%d.%m.%Y}" if user and user.created_at else "—"
    uname = f"@{user.username}" if user and user.username else "—"
    sub_line = t(lang, "set_sub_active", title=_tariff_title(active.tariff)) if active else t(lang, "set_sub_none")
    conn = t(lang, "conn_yes") if has_account else t(lang, "conn_no")

    lines = [
        t(lang, "set_title") + "\n",
        t(lang, "set_joined", date=joined),
        t(lang, "set_username", uname=uname),
        t(lang, "set_sub", sub=sub_line),
        t(lang, "set_account", conn=conn),
    ]
    if active and user is not None:
        quota = get_plan(Tariff(active.tariff)).check_quota
        used = (await queries.peek_check_quota(db, user.id, quota)).used
        lines.append(t(lang, "set_check_quota", left=max(quota - used, 0), total=quota))

    auto_renew = bool(active and active.auto_renew and active.order_reference)
    if auto_renew:
        lines.append(t(lang, "set_autorenew_on"))

    can_record = bool(active and tariff_grants_command(active.tariff, "save"))
    return "\n".join(lines), _main_menu(
        has_account, lang, auto_renew=auto_renew, can_record=can_record
    )


async def _has_active_sub(telegram_id: int) -> bool:
    async with get_session() as db:
        return await queries.get_active_subscription_by_telegram_id(db, telegram_id) is not None


def _me_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(lang, "me_btn_text"), callback_data="set:me:text"),
                InlineKeyboardButton(text=t(lang, "me_btn_link"), callback_data="set:me:link"),
            ],
            [
                InlineKeyboardButton(text=t(lang, "me_btn_photo"), callback_data="set:me:photo"),
                InlineKeyboardButton(text=t(lang, "me_btn_buttons"), callback_data="set:me:buttons"),
            ],
            [
                InlineKeyboardButton(text=t(lang, "me_btn_view"), callback_data="set:me:view"),
                InlineKeyboardButton(text=t(lang, "me_btn_clear"), callback_data="set:me:clear"),
            ],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="set:back")],
        ]
    )


def _ar_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "ar_btn_toggle"), callback_data="set:ar:toggle")],
            [
                InlineKeyboardButton(text=t(lang, "ar_btn_msg"), callback_data="set:ar:msg"),
                InlineKeyboardButton(text=t(lang, "me_btn_photo"), callback_data="set:ar:photo"),
            ],
            [
                InlineKeyboardButton(text=t(lang, "ar_btn_time"), callback_data="set:ar:time"),
                InlineKeyboardButton(text=t(lang, "ar_btn_exc"), callback_data="set:ar:exc"),
            ],
            [
                InlineKeyboardButton(text=t(lang, "me_btn_buttons"), callback_data="set:ar:buttons"),
                InlineKeyboardButton(text=t(lang, "me_btn_view"), callback_data="set:ar:view"),
            ],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="set:back")],
        ]
    )


def _lang_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "lang_uk"), callback_data="set:lang:uk")],
            [InlineKeyboardButton(text=t(lang, "lang_ru"), callback_data="set:lang:ru")],
            [InlineKeyboardButton(text=t(lang, "lang_en"), callback_data="set:lang:en")],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="set:back")],
        ]
    )


def _feat_label(lang: str, key: str, enabled: bool) -> str:
    mark = "✅" if enabled else "❌"
    return f"{mark} {t(lang, key)}"


_FEATURE_ROWS = [
    [("deleted", "feat_deleted"), ("edited", "feat_edited")],
    [("info", "feat_info"), ("me", "feat_me")],
    [("ban", "feat_ban"), ("check", "feat_check")],
    [("viewonce_photo", "feat_viewonce_photo"), ("viewonce_voice", "feat_viewonce_voice")],
    [("viewonce_video", "feat_viewonce_video"), ("send", "feat_send")],
    [("mute", "feat_mute"), ("record", "feat_record")],
    [("clone", "feat_clone")],
]

# Switches whose feature the tariff may not include at all. A switch for
# something the plan does not grant is a lie either way round: off looks like
# the reason it does not work, on promises something that will not happen.
_TARIFF_GATED = {"send": "send", "mute": "mute", "record": "save", "clone": "clone"}


def _features_menu(features: Features, lang: str, *, tariff: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    for row in _FEATURE_ROWS:
        buttons = [
            InlineKeyboardButton(
                text=_feat_label(lang, label_key, getattr(features, field)),
                callback_data=f"set:features:toggle:{field}",
            )
            for field, label_key in row
            if field not in _TARIFF_GATED
            or (tariff is not None and tariff_grants_command(tariff, _TARIFF_GATED[field]))
        ]
        if buttons:
            rows.append(buttons)
    rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="set:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ignored_menu(chats: list, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"✕ {c.chat_title or c.chat_id}", callback_data=f"set:ignored:rm:{c.chat_id}")]
        for c in chats
    ]
    rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="set:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- entry -----------------------------------------------------------------


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    lang = lang_of(message)
    async with get_session() as db:
        await upsert_user(db, message.chat.id, language_code=message.from_user.language_code if message.from_user else None)
        await db.commit()
        text, kb = await _build_settings(db, message.chat.id, lang)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "set:back")
async def on_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = lang_of(callback)
    async with get_session() as db:
        text, kb = await _build_settings(db, callback.message.chat.id, lang)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "set:autorenew:cancel")
async def on_cancel_autorenew(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    telegram_id = callback.message.chat.id
    async with get_session() as db:
        sub = await queries.get_active_subscription_by_telegram_id(db, telegram_id)
        if sub is None or not sub.auto_renew or not sub.order_reference:
            await callback.answer()
            return
        order_reference, expires_at = sub.order_reference, sub.expires_at

    provider = build_wayforpay()
    if provider is None:
        await callback.answer(t(lang, "set_autorenew_cancel_failed"), show_alert=True)
        return

    # Cancel at the gateway BEFORE clearing our flag. The other order would
    # show "cancelled" to a user whose card is still being charged every
    # month — the worst possible failure mode for a standing mandate.
    try:
        await provider.remove_regular(order_reference)
    except Exception:
        logger.exception("wayforpay: failed to remove regular payment %s", order_reference)
        await callback.answer(t(lang, "set_autorenew_cancel_failed"), show_alert=True)
        return

    async with get_session() as db:
        sub = await queries.get_active_subscription_by_telegram_id(db, telegram_id)
        if sub is not None:
            sub.auto_renew = False
            await db.commit()
        text, kb = await _build_settings(db, telegram_id, lang)

    date = f"{expires_at:%d.%m.%Y}" if expires_at else "—"
    await callback.answer(t(lang, "set_autorenew_cancelled", date=date), show_alert=True)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "set:me")
async def on_me(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    if not await _has_active_sub(callback.message.chat.id):
        await callback.answer(t(lang, "need_active_sub_alert"), show_alert=True)
        return
    await callback.message.edit_text(t(lang, "set_me_title"), reply_markup=_me_menu(lang))
    await callback.answer()


@router.callback_query(F.data == "set:ar")
async def on_ar(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    if not await _has_active_sub(callback.message.chat.id):
        await callback.answer(t(lang, "need_active_sub_alert"), show_alert=True)
        return
    await callback.message.edit_text(t(lang, "set_ar_title"), reply_markup=_ar_menu(lang))
    await callback.answer()


@router.callback_query(F.data == "set:ignored")
async def on_ignored(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        user = await queries.get_user_by_telegram_id(db, callback.message.chat.id)
        chats = await queries.list_ignored_chats(db, user.id) if user else []
    header = t(lang, "ignored_header") if chats else t(lang, "ignored_empty")
    await callback.message.edit_text(header, reply_markup=_ignored_menu(chats, lang))
    await callback.answer()


@router.callback_query(F.data.startswith("set:ignored:rm:"))
async def on_ignored_remove(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    chat_id = int(callback.data.rsplit(":", 1)[1])
    async with get_session() as db:
        user = await queries.get_user_by_telegram_id(db, callback.message.chat.id)
        if user is not None:
            await queries.remove_ignored_chat(db, user.id, chat_id)
            await db.commit()
        chats = await queries.list_ignored_chats(db, user.id) if user else []
    header = t(lang, "ignored_header") if chats else t(lang, "ignored_empty")
    await callback.message.edit_text(header, reply_markup=_ignored_menu(chats, lang))
    await callback.answer(t(lang, "ignored_removed"))


async def _load_features(session, telegram_id: int) -> tuple[int | None, Features]:
    user = await queries.get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return None, Features()
    row = await queries.get_or_create_settings(session, user.id)
    return user.id, Features.from_dict(row.features)


async def _current_tariff(db, telegram_id: int) -> str | None:
    sub = await queries.get_active_subscription_by_telegram_id(db, telegram_id)
    return sub.tariff if sub is not None else None


@router.callback_query(F.data == "set:menu")
async def on_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Back to the dashboard. The settings screen is replaced rather than
    added to — leaving it behind would put two live keyboards in the chat,
    and the stale one keeps answering."""
    from management_bot.handlers.start import show_dashboard

    await state.clear()
    await show_dashboard(callback.message)
    try:
        await callback.message.delete()
    except Exception:
        logger.debug("could not clear the settings screen", exc_info=True)
    await callback.answer()


@router.callback_query(F.data == "set:features")
async def on_features(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        _, features = await _load_features(db, callback.message.chat.id)
        tariff = await _current_tariff(db, callback.message.chat.id)
    await callback.message.edit_text(
        t(lang, "features_title"), reply_markup=_features_menu(features, lang, tariff=tariff)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set:features:toggle:"))
async def on_features_toggle(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    key = callback.data.rsplit(":", 1)[1]
    async with get_session() as db:
        uid, features = await _load_features(db, callback.message.chat.id)
        if uid is None:
            await callback.answer()
            return
        setattr(features, key, not getattr(features, key))
        await queries.set_features(db, uid, features.to_dict())
        await db.commit()
        tariff = await _current_tariff(db, callback.message.chat.id)
    await callback.message.edit_text(
        t(lang, "features_title"), reply_markup=_features_menu(features, lang, tariff=tariff)
    )
    await callback.answer()


@router.callback_query(F.data == "set:lang")
async def on_lang(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    await callback.message.edit_text(t(lang, "lang_title"), reply_markup=_lang_menu(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("set:lang:"))
async def on_lang_pick(callback: CallbackQuery) -> None:
    chosen = callback.data.rsplit(":", 1)[1]
    async with get_session() as db:
        await queries.set_user_language_manual(db, callback.message.chat.id, chosen)
        await db.commit()
        text, kb = await _build_settings(db, callback.message.chat.id, chosen)
    await callback.answer(t(chosen, "lang_saved"))
    await callback.message.edit_text(text, reply_markup=kb)
    # the persistent reply-keyboard labels only refresh on a new message
    await callback.message.answer(t(chosen, "lang_saved"), reply_markup=keyboards.main_menu(chosen))


@router.callback_query(F.data == "set:link")
async def on_link(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await connect.cmd_connect(callback.message, state)


@router.callback_query(F.data == "set:unlink")
async def on_unlink(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        user = await queries.get_user_by_telegram_id(db, callback.message.chat.id)
        if user is not None:
            await queries.deactivate_user_sessions(db, user.id)
            await db.commit()
    await callback.answer(t(lang, "account_unlinked"), show_alert=True)
    async with get_session() as db:
        text, kb = await _build_settings(db, callback.message.chat.id, lang)
    await callback.message.edit_text(text, reply_markup=kb)


# --- prompts ---------------------------------------------------------------


_ME_PROMPTS = {
    "set:me:text": (SettingsStates.me_text, "prompt_me_text"),
    "set:me:link": (SettingsStates.me_link, "prompt_me_link"),
    "set:me:photo": (SettingsStates.me_photo, "prompt_me_photo"),
    "set:me:buttons": (SettingsStates.me_buttons, "prompt_me_buttons"),
}
_AR_PROMPTS = {
    "set:ar:msg": (SettingsStates.ar_message, "prompt_ar_msg"),
    "set:ar:photo": (SettingsStates.ar_photo, "prompt_ar_photo"),
    "set:ar:buttons": (SettingsStates.ar_buttons, "prompt_me_buttons"),
    "set:ar:time": (SettingsStates.ar_time, "prompt_ar_time"),
    "set:ar:exc": (SettingsStates.ar_exceptions, "prompt_ar_exc"),
}


@router.callback_query(F.data.in_(set(_ME_PROMPTS) | set(_AR_PROMPTS)))
async def on_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    lang = lang_of(callback)
    prompts = _ME_PROMPTS if callback.data in _ME_PROMPTS else _AR_PROMPTS
    target_state, key = prompts[callback.data]
    await state.set_state(target_state)
    await callback.message.answer(t(lang, key))
    await callback.answer()


# --- me card: apply input --------------------------------------------------


async def _load_me(session, telegram_id) -> tuple[int, MeCard]:
    user = await upsert_user(session, telegram_id)
    row = await queries.get_or_create_settings(session, user.id)
    return user.id, MeCard.from_dict(row.me_card)


@router.message(SettingsStates.me_text, F.text)
async def set_me_text(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    async with get_session() as db:
        uid, card = await _load_me(db, message.chat.id)
        card.text = message.text.strip()
        await queries.set_me_card(db, uid, card.to_dict())
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_text"), reply_markup=_me_menu(lang))


@router.message(SettingsStates.me_link, F.text)
async def set_me_link(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    value = None if message.text.strip() == "-" else message.text.strip()
    # Validated here rather than at render time: an unusable link makes .me
    # fail inside a chat with a client, and that failure is only logged — the
    # owner would never learn why their card stopped working.
    if value is not None and not is_valid_url(value):
        await message.answer(t(lang, "me_link_invalid"))
        return
    async with get_session() as db:
        uid, card = await _load_me(db, message.chat.id)
        card.link = value
        await queries.set_me_card(db, uid, card.to_dict())
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_link"), reply_markup=_me_menu(lang))


@router.message(SettingsStates.me_photo, F.photo)
async def set_me_photo(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    data = await _download_photo(message)
    async with get_session() as db:
        uid, card = await _load_me(db, message.chat.id)
        await queries.set_media(db, uid, "me", data, mime="image/jpeg")
        card.has_photo = True
        await queries.set_me_card(db, uid, card.to_dict())
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_photo"), reply_markup=_me_menu(lang))


@router.message(SettingsStates.me_buttons, F.text)
async def set_me_buttons(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    buttons = parse_buttons(message.text)
    async with get_session() as db:
        uid, card = await _load_me(db, message.chat.id)
        card.buttons = buttons
        await queries.set_me_card(db, uid, card.to_dict())
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_buttons", n=len(buttons)), reply_markup=_me_menu(lang))


@router.callback_query(F.data == "set:me:view")
async def view_me(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        _, card = await _load_me(db, callback.message.chat.id)
    await callback.message.answer(_render_me_preview(card, lang))
    await callback.answer()


@router.callback_query(F.data == "set:me:clear")
async def clear_me(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        user = await upsert_user(db, callback.message.chat.id)
        await queries.set_me_card(db, user.id, None)
        await queries.delete_media(db, user.id, "me")
        await db.commit()
    await callback.answer(t(lang, "me_cleared"), show_alert=True)


# --- autoresponder: apply input -------------------------------------------


async def _load_ar(session, telegram_id) -> tuple[int, Autoresponder]:
    user = await upsert_user(session, telegram_id)
    row = await queries.get_or_create_settings(session, user.id)
    return user.id, Autoresponder.from_dict(row.autoresponder)


async def _save_ar(session, uid, ar: Autoresponder) -> None:
    await queries.set_autoresponder(session, uid, ar.to_dict())


@router.callback_query(F.data == "set:ar:toggle")
async def toggle_ar(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        uid, ar = await _load_ar(db, callback.message.chat.id)
        ar.enabled = not ar.enabled
        await _save_ar(db, uid, ar)
        await db.commit()
    state_word = t(lang, "ar_state_on") if ar.enabled else t(lang, "ar_state_off")
    await callback.answer(t(lang, "ar_toggled", state=state_word), show_alert=True)


@router.message(SettingsStates.ar_message, F.text)
async def set_ar_message(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    async with get_session() as db:
        uid, ar = await _load_ar(db, message.chat.id)
        ar.message = message.text.strip()
        await _save_ar(db, uid, ar)
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_message"), reply_markup=_ar_menu(lang))


@router.message(SettingsStates.ar_photo, F.photo)
async def set_ar_photo(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    data = await _download_photo(message)
    async with get_session() as db:
        uid, ar = await _load_ar(db, message.chat.id)
        await queries.set_media(db, uid, "autoresponder", data, mime="image/jpeg")
        ar.has_photo = True
        await _save_ar(db, uid, ar)
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_photo"), reply_markup=_ar_menu(lang))


@router.message(SettingsStates.ar_buttons, F.text)
async def set_ar_buttons(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    buttons = parse_buttons(message.text)
    async with get_session() as db:
        uid, ar = await _load_ar(db, message.chat.id)
        ar.buttons = buttons
        await _save_ar(db, uid, ar)
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_buttons", n=len(buttons)), reply_markup=_ar_menu(lang))


@router.message(SettingsStates.ar_time, F.text)
async def set_ar_time(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    raw = message.text.strip()
    start = end = None
    if raw != "-" and "-" in raw:
        a, _, b = raw.partition("-")
        start, end = parse_hhmm(a), parse_hhmm(b)
    async with get_session() as db:
        uid, ar = await _load_ar(db, message.chat.id)
        ar.active_from, ar.active_to = start, end
        await _save_ar(db, uid, ar)
        await db.commit()
    await state.clear()
    window = f"{start}-{end}" if start and end else t(lang, "window_allday")
    await message.answer(t(lang, "saved_time", window=window), reply_markup=_ar_menu(lang))


@router.message(SettingsStates.ar_exceptions, F.text)
async def set_ar_exceptions(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    raw = message.text.strip()
    ids: list[int] = []
    if raw != "-":
        for token in raw.replace(",", " ").split():
            try:
                ids.append(int(token))
            except ValueError:
                continue
    async with get_session() as db:
        uid, ar = await _load_ar(db, message.chat.id)
        ar.exceptions = ids
        await _save_ar(db, uid, ar)
        await db.commit()
    await state.clear()
    await message.answer(t(lang, "saved_exc", n=len(ids)), reply_markup=_ar_menu(lang))


@router.callback_query(F.data == "set:ar:view")
async def view_ar(callback: CallbackQuery) -> None:
    async with get_session() as db:
        _, ar = await _load_ar(db, callback.message.chat.id)
    await callback.message.answer(_render_ar_preview(ar, lang_of(callback)))
    await callback.answer()


# --- helpers ---------------------------------------------------------------


async def _download_photo(message: Message) -> bytes:
    buffer = await message.bot.download(message.photo[-1])
    return buffer.read()


def _render_me_preview(card: MeCard, lang: str) -> str:
    if card.is_empty():
        return t(lang, "preview_empty")
    lines = [card.text or t(lang, "preview_no_text")]
    if card.link:
        lines.append(f"🔗 {card.link}")
    if card.has_photo:
        lines.append("🖼 …")
    if card.buttons:
        lines.append("🔘 " + ", ".join(b.text for b in card.buttons))
    return "\n".join(lines)


def _render_ar_preview(ar: Autoresponder, lang: str) -> str:
    window = f"{ar.active_from}-{ar.active_to}" if ar.active_from and ar.active_to else t(lang, "window_allday")
    state_word = t(lang, "ar_state_on") if ar.enabled else t(lang, "ar_state_off")
    return (
        f"{t(lang, 'ar_toggled', state=state_word)}\n"
        f"{ar.message or '—'}\n"
        f"🖼 {'✓' if ar.has_photo else '✗'} · 🕒 {window} · 🚫 {len(ar.exceptions)} · 🔘 {len(ar.buttons)}"
    )


# --- chat recording --------------------------------------------------------


def _recordings_menu(rows, lang: str) -> InlineKeyboardMarkup:
    """One stop button per running recording, plus the picker."""
    buttons = [
        [
            InlineKeyboardButton(
                text=t(lang, "rec_btn_stop", chat=row.chat_title or row.chat_id),
                callback_data=f"set:rec:stop:{row.id}",
            )
        ]
        for row in rows
    ]
    buttons.append([InlineKeyboardButton(text=t(lang, "rec_btn_pick"), callback_data="set:rec:pick")])
    buttons.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="set:back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _recordings_screen(db, telegram_id: int, lang: str):
    user = await queries.get_user_by_telegram_id(db, telegram_id)
    rows = await queries.get_active_recordings(db, user.id) if user else []
    text = t(lang, "rec_menu_title")
    if not rows:
        text = f"{text}\n\n{t(lang, 'rec_menu_none')}"
    return text, _recordings_menu(rows, lang)


@router.callback_query(F.data == "set:rec")
async def on_recordings(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        text, kb = await _recordings_screen(db, callback.message.chat.id, lang)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "set:rec:pick")
async def on_recordings_pick(callback: CallbackQuery, state: FSMContext) -> None:
    """Telegram's own contact picker. The bot has no idea what dialogs someone
    has; this asks them and hands back the id, which for a private chat is the
    chat id too."""
    lang = lang_of(callback)
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=t(lang, "rec_pick_button"),
                    request_users=KeyboardButtonRequestUsers(
                        request_id=_RECORD_REQUEST_ID,
                        user_is_bot=False,
                        max_quantity=1,
                        request_name=True,
                        request_username=True,
                    ),
                )
            ],
            [KeyboardButton(text=t(lang, "rec_pick_cancel"))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    prompt = await callback.message.answer(t(lang, "rec_pick_prompt"), reply_markup=keyboard)
    # Remembered so the prompt can be taken back down with the reply it asked
    # for; a restart only costs one stranded line, not a broken flow.
    await state.update_data(rec_prompt_id=prompt.message_id)
    await callback.answer()


async def _clear_pick(message: Message, state: FSMContext) -> None:
    """Take the picker's paper trail back down.

    The prompt and Telegram's "you shared a contact" line are both machinery,
    not conversation, and they were stacking up a fresh pair on screen every
    time someone opened the picker.
    """
    data = await state.get_data()
    prompt_id = data.get("rec_prompt_id")
    if prompt_id is not None:
        await state.update_data(rec_prompt_id=None)
    for message_id in (prompt_id, message.message_id):
        if message_id is None:
            continue
        # Older than 48h, or already gone — nothing here is worth an error.
        with contextlib.suppress(TelegramBadRequest):
            await message.bot.delete_message(message.chat.id, message_id)


@router.message(F.text.in_(variants("rec_pick_cancel")))
async def on_pick_cancel(message: Message, state: FSMContext) -> None:
    """Without this the cancel button is a lie: it sends a word the bot has no
    handler for, so the picker keyboard stays up and nothing answers."""
    lang = lang_of(message)
    await _clear_pick(message, state)
    await message.answer(t(lang, "rec_pick_cancelled"), reply_markup=keyboards.main_menu(lang))


@router.message(F.users_shared)
async def on_user_picked(message: Message, state: FSMContext) -> None:
    lang = lang_of(message)
    shared = message.users_shared
    if shared.request_id != _RECORD_REQUEST_ID or not shared.user_ids:
        return
    await _clear_pick(message, state)

    chat_id = shared.user_ids[0]
    title = _shared_name(shared) or str(chat_id)

    async with get_session() as db:
        user = await queries.get_user_by_telegram_id(db, message.chat.id)
        if user is None:
            await message.answer(t(lang, "rec_needs_account"), reply_markup=keyboards.main_menu(lang))
            return
        running = {row.chat_id for row in await queries.get_active_recordings(db, user.id)}
        if chat_id in running:
            await message.answer(t(lang, "rec_already_for"), reply_markup=keyboards.main_menu(lang))
            return
        await queries.start_recording(db, owner_user_id=user.id, chat_id=chat_id, chat_title=title)
        await db.commit()

    # The worker polls for this, so it starts collecting within seconds rather
    # than instantly — worth saying plainly rather than implying otherwise.
    await message.answer(
        t(lang, "rec_started_for", chat=title), reply_markup=keyboards.main_menu(lang)
    )


def _shared_name(shared) -> str | None:
    users = getattr(shared, "users", None) or []
    if not users:
        return None
    user = users[0]
    if getattr(user, "username", None):
        return f"@{user.username}"
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    return " ".join(p for p in parts if p) or None


@router.callback_query(F.data.startswith("set:rec:stop:"))
async def on_recording_stop(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    recording_id = int(callback.data.rsplit(":", 1)[1])

    async with get_session() as db:
        user = await queries.get_user_by_telegram_id(db, callback.message.chat.id)
        rows = await queries.get_active_recordings(db, user.id) if user else []
        target = next((r for r in rows if r.id == recording_id), None)
        if target is None:
            # Already stopped, from here or with .unsave in the chat.
            text, kb = await _recordings_screen(db, callback.message.chat.id, lang)
            await callback.message.edit_text(text, reply_markup=kb)
            await callback.answer()
            return

        title = target.chat_title or str(target.chat_id)
        await queries.stop_recording(db, recording_id)
        messages = await queries.get_recorded_messages(db, recording_id)
        await db.commit()
        archive = build_archive(title, messages, lang)
        text, kb = await _recordings_screen(db, callback.message.chat.id, lang)

    if messages:
        await callback.message.answer_document(
            BufferedInputFile(archive.encode("utf-8"), filename=f"chat-{target.chat_id}.txt"),
            caption=t(lang, "rec_stopped_for", count=len(messages)),
        )
    else:
        await callback.message.answer(t(lang, "rec_stopped_empty"))

    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()
