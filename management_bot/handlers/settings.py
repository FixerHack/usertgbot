"""/settings: profile + configure the `.me` card (Standard), autoresponder
(Pro), and the ignored-chats list. Values live in db.UserSettings (JSON) /
db.MediaBlob / db.IgnoredChat; the userbot reads them at runtime."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db import queries
from db.session import get_session
from management_bot import keyboards, storage
from management_bot.handlers import connect
from management_bot.storage import upsert_user
from shared.i18n import lang_of, t
from shared.settings_schema import Autoresponder, Features, MeCard, parse_buttons, parse_hhmm
from shared.tariffs import Tariff, get_plan

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


def _main_menu(has_account: bool, lang: str) -> InlineKeyboardMarkup:
    account_btn = (
        InlineKeyboardButton(text=t(lang, "set_btn_unlink"), callback_data="set:unlink")
        if has_account
        else InlineKeyboardButton(text=t(lang, "set_btn_link"), callback_data="set:link")
    )
    rows = [
        [
            InlineKeyboardButton(text=t(lang, "set_btn_me"), callback_data="set:me"),
            InlineKeyboardButton(text=t(lang, "set_btn_ar"), callback_data="set:ar"),
            InlineKeyboardButton(text=t(lang, "set_btn_ignored"), callback_data="set:ignored"),
        ],
        [
            InlineKeyboardButton(text=t(lang, "set_btn_features"), callback_data="set:features"),
            InlineKeyboardButton(text=t(lang, "set_btn_lang"), callback_data="set:lang"),
            account_btn,
        ],
    ]
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

    return "\n".join(lines), _main_menu(has_account, lang)


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
]


def _features_menu(features: Features, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=_feat_label(lang, label_key, getattr(features, field)),
                callback_data=f"set:features:toggle:{field}",
            )
            for field, label_key in row
        ]
        for row in _FEATURE_ROWS
    ]
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


@router.callback_query(F.data == "set:features")
async def on_features(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    async with get_session() as db:
        _, features = await _load_features(db, callback.message.chat.id)
    await callback.message.edit_text(t(lang, "features_title"), reply_markup=_features_menu(features, lang))
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
    await callback.message.edit_text(t(lang, "features_title"), reply_markup=_features_menu(features, lang))
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
