"""Lightweight 3-language i18n (UK / RU / EN), no external deps.

Language is chosen from the Telegram `language_code` (aiogram) / `lang_code`
(Telethon). Strings live here as `(uk, ru, en)` tuples so every key has all
three in one place; `t(lang, key, **kwargs)` formats one. Reply-keyboard
buttons are matched across languages via `variants(key)`.

Telegram's `language_code` reflects the client's initial/device locale and
does not reliably follow a later in-app "Settings > Language" change, so a
user can pick a language explicitly (stored as `User.language_code` +
`language_locked=True`). A per-update contextvar lets a middleware publish
that DB-resolved language once per update; every `lang_of(event)` call below
it (handlers, keyboards, etc.) then picks it up with no call-site changes.
"""

from __future__ import annotations

from contextvars import ContextVar

_LANG_IDX = {"uk": 0, "ru": 1, "en": 2}
DEFAULT_LANG = "uk"

_lang_override: ContextVar[str | None] = ContextVar("_lang_override", default=None)


def set_lang_override(lang: str | None):
    """Publish the DB-resolved language for the current update. Returns a
    reset token — callers (middleware) must `_lang_override.reset(token)`
    when the update is done, so contextvar state can't leak across updates."""
    return _lang_override.set(lang)


def reset_lang_override(token) -> None:
    _lang_override.reset(token)


def resolve_lang(language_code: str | None) -> str:
    """Map a Telegram language code to one of our supported languages."""
    if not language_code:
        return DEFAULT_LANG
    code = language_code[:2].lower()
    if code in ("uk", "ru", "en"):
        return code
    return "en"  # anything else falls back to English


def lang_of(event) -> str:
    """Resolve language: the current update's DB override wins if set,
    otherwise falls back to an aiogram Message/CallbackQuery's `from_user`."""
    override = _lang_override.get()
    if override is not None:
        return override
    user = getattr(event, "from_user", None)
    return resolve_lang(getattr(user, "language_code", None) if user else None)


def t(lang: str, key: str, **kwargs) -> str:
    row = _TR.get(key)
    if row is None:
        return key
    text = row[_LANG_IDX.get(lang, 0)]
    return text.format(**kwargs) if kwargs else text


def variants(key: str) -> set[str]:
    """All translations of a key — used to match localized reply buttons."""
    row = _TR.get(key)
    return set(row) if row else set()


def features(lang: str, tariff_id: str) -> list[str]:
    row = _FEATURES.get(tariff_id)
    if row is None:
        return []
    return row[_LANG_IDX.get(lang, 0)]


# --- strings: (uk, ru, en) -------------------------------------------------

_TR: dict[str, tuple[str, str, str]] = {
    # start / dashboard
    "start_header": (
        "🤖 Привіт! Я <b>User Agent Bot</b>",
        "🤖 Привет! Я <b>User Agent Bot</b>",
        "🤖 Hi! I'm <b>User Agent Bot</b>",
    ),
    "dash_sub": (
        "💳 Підписка: <b>{tariff}</b> ({status}){expires}",
        "💳 Подписка: <b>{tariff}</b> ({status}){expires}",
        "💳 Subscription: <b>{tariff}</b> ({status}){expires}",
    ),
    "dash_sub_none": (
        "💳 Підписка: <b>немає</b>",
        "💳 Подписка: <b>нет</b>",
        "💳 Subscription: <b>none</b>",
    ),
    "dash_expires": (" до {date}", " до {date}", " until {date}"),
    "sub_state_expired_on": ("завершилась {date}", "завершилась {date}", "ended {date}"),
    # The subscription state as a word. It used to reach the screen as the raw
    # database value ("active"), untranslated, in every language.
    "sub_state_active": ("активна", "активна", "active"),
    "sub_state_expired": ("завершилась", "завершилась", "expired"),
    "sub_state_pending": ("очікує оплати", "ожидает оплаты", "awaiting payment"),
    "sub_state_cancelled": ("скасована", "отменена", "cancelled"),
    "dash_account": ("🔌 Акаунт: <b>{conn}</b>", "🔌 Аккаунт: <b>{conn}</b>", "🔌 Account: <b>{conn}</b>"),
    "conn_yes": ("✅ підключено", "✅ подключён", "✅ connected"),
    "conn_no": ("❌ не підключено", "❌ не подключён", "❌ not connected"),
    # reply keyboard
    "btn_subscribe": ("💳 Тарифи", "💳 Тарифы", "💳 Plans"),
    "btn_settings": ("⚙️ Налаштування", "⚙️ Настройки", "⚙️ Settings"),
    "btn_help": ("❓ Довідка", "❓ Помощь", "❓ Help"),
    "btn_support": ("🆘 Підтримка", "🆘 Поддержка", "🆘 Support"),
    "btn_menu": ("🏠 Меню", "🏠 Меню", "🏠 Menu"),
    # gating / help / support
    "need_subscription": (
        "🔒 Спочатку оформіть підписку — натисніть «💳 Тарифи».",
        "🔒 Сначала оформите подписку — нажмите «💳 Тарифы».",
        "🔒 Get a subscription first — tap «💳 Plans».",
    ),
    "need_active_sub_alert": (
        "🔒 Потрібна активна підписка",
        "🔒 Нужна активная подписка",
        "🔒 An active subscription is required",
    ),
    # --- help: shared sections, reused by every plan's view ---
    "help_bot_section": (
        "<b>Керуючий бот</b> (цей чат)\n"
        "• 🏠 Меню — головний екран: статус підписки й акаунта\n"
        "• 💳 Тарифи — оформити або змінити підписку\n"
        "• ⚙️ Налаштування — візитка, автовідповідач, ігноровані чати, мова, керування акаунтом\n"
        "• 🆘 Підтримка — зв'язок із менеджером",
        "<b>Управляющий бот</b> (этот чат)\n"
        "• 🏠 Меню — главный экран: статус подписки и аккаунта\n"
        "• 💳 Тарифы — оформить или изменить подписку\n"
        "• ⚙️ Настройки — визитка, автоответчик, игнорируемые чаты, язык, управление аккаунтом\n"
        "• 🆘 Поддержка — связь с менеджером",
        "<b>Management bot</b> (this chat)\n"
        "• 🏠 Menu — home screen: subscription and account status\n"
        "• 💳 Plans — buy or change your subscription\n"
        "• ⚙️ Settings — your card, autoresponder, ignored chats, language, account\n"
        "• 🆘 Support — reach a manager",
    ),
    "help_manager_section": (
        "<b>Менеджер-бот</b>\n"
        "Окремий бот, куди приходять усі сповіщення: видалені й змінені повідомлення, "
        "спрацювання автовідповідача, результати <code>.info</code> та <code>.check</code>, "
        "одноразові фото й голосові. Запустити його — обов'язковий крок після підключення "
        "акаунта, без цього сповіщень не буде.",
        "<b>Менеджер-бот</b>\n"
        "Отдельный бот, куда приходят все уведомления: удалённые и изменённые сообщения, "
        "срабатывания автоответчика, результаты <code>.info</code> и <code>.check</code>, "
        "одноразовые фото и голосовые. Запустить его — обязательный шаг после подключения "
        "аккаунта, без этого уведомлений не будет.",
        "<b>Manager bot</b>\n"
        "A separate bot where every notification arrives: deleted and edited messages, "
        "autoresponder replies, <code>.info</code> and <code>.check</code> results, "
        "one-time photos and voice notes. Starting it is a required step after linking "
        "your account — without it you get no notifications at all.",
    ),
    "help_how_commands": (
        "Команди пишете <b>зі свого підключеного акаунта</b> прямо в переписці, "
        "а не в цьому чаті.",
        "Команды пишете <b>со своего подключённого аккаунта</b> прямо в переписке, "
        "а не в этом чате.",
        "You type commands <b>from your linked account</b>, right inside a conversation — "
        "not in this chat.",
    ),
    "help_autosave_section": (
        "<b>Автозбереження</b>\n"
        "Якщо співрозмовник видалить або змінить повідомлення — копія прийде в менеджер-бот. "
        "Працює і для медіа: фото, голосові, відео, документи, локації. "
        "Вмикається окремими перемикачами в ⚙️ Налаштування.",
        "<b>Автосохранение</b>\n"
        "Если собеседник удалит или изменит сообщение — копия придёт в менеджер-бот. "
        "Работает и для медиа: фото, голосовые, видео, документы, локации. "
        "Включается отдельными переключателями в ⚙️ Настройки.",
        "<b>Auto-save</b>\n"
        "If someone deletes or edits a message, a copy lands in your manager bot. "
        "Works for media too: photos, voice notes, video, documents, locations. "
        "Toggled per type in ⚙️ Settings.",
    ),
    # --- help: one view per plan. {bot}/{manager}/{how}/{autosave} are
    # filled from the shared sections above, so the wording can't drift
    # apart between the per-plan views and the full one ---
    "help_all": (
        "❓ <b>Довідка — весь функціонал</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Команди в переписках</b>\n"
        "{how}\n"
        "• <code>.info</code> — зводка по співрозмовнику або чату + аватарка\n"
        "• <code>.me</code> — надіслати свою візитку\n"
        "• <code>.ban</code> — в особистих: заблокувати й видалити; у групі: вийти\n"
        "• <code>.check</code> — перевірка (ліміт залежить від тарифу)\n"
        "• <code>.send N текст</code> — надіслати N копій повідомлення\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Автовідповідач</b>\n"
        "Автоматична відповідь на вхідні, поки вас немає. Налаштовується в ⚙️ Налаштування.\n"
        "\n"
        "<b>Що дає який тариф</b>\n"
        "• <b>Standard</b> — .info, .me, .ban, .check (5/міс), автозбереження\n"
        "• <b>Pro</b> — усе зі Standard + .check 10/міс + автовідповідач + .send (до 50 копій, раз на 10 хв)\n"
        "• <b>Premium</b> — усе з Pro + .check 30/міс + .send (до 100 копій, раз на 2 хв)",
        "❓ <b>Помощь — весь функционал</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Команды в переписках</b>\n"
        "{how}\n"
        "• <code>.info</code> — сводка по собеседнику или чату + аватарка\n"
        "• <code>.me</code> — отправить свою визитку\n"
        "• <code>.ban</code> — в личных: заблокировать и удалить; в группе: выйти\n"
        "• <code>.check</code> — проверка (лимит зависит от тарифа)\n"
        "• <code>.send N текст</code> — отправить N копий сообщения\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Автоответчик</b>\n"
        "Автоматический ответ на входящие, пока вас нет. Настраивается в ⚙️ Настройки.\n"
        "\n"
        "<b>Что даёт какой тариф</b>\n"
        "• <b>Standard</b> — .info, .me, .ban, .check (5/мес), автосохранение\n"
        "• <b>Pro</b> — всё из Standard + .check 10/мес + автоответчик + .send (до 50 копий, раз в 10 мин)\n"
        "• <b>Premium</b> — всё из Pro + .check 30/мес + .send (до 100 копий, раз в 2 мин)",
        "❓ <b>Help — everything the service does</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>In-conversation commands</b>\n"
        "{how}\n"
        "• <code>.info</code> — summary of the person or chat + their avatar\n"
        "• <code>.me</code> — send your own card\n"
        "• <code>.ban</code> — in DMs: block and delete; in a group: leave\n"
        "• <code>.check</code> — lookup (allowance depends on your plan)\n"
        "• <code>.send N text</code> — post N copies of a message\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Autoresponder</b>\n"
        "Replies to incoming messages automatically while you're away. Set it up in ⚙️ Settings.\n"
        "\n"
        "<b>What each plan includes</b>\n"
        "• <b>Standard</b> — .info, .me, .ban, .check (5/mo), auto-save\n"
        "• <b>Pro</b> — everything in Standard + .check 10/mo + autoresponder + .send (up to 50 copies, once per 10 min)\n"
        "• <b>Premium</b> — everything in Pro + .check 30/mo + .send (up to 100 copies, once per 2 min)",
    ),
    "help_standard": (
        "❓ <b>Довідка — ваш тариф: Standard</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Ваші команди</b>\n"
        "{how}\n"
        "• <code>.info</code> — зводка по співрозмовнику або чату + аватарка\n"
        "• <code>.me</code> — надіслати свою візитку\n"
        "• <code>.ban</code> — в особистих: заблокувати й видалити; у групі: вийти\n"
        "• <code>.check</code> — <b>5 перевірок на місяць</b>, лічильник оновлюється 1-го числа\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Не входить у Standard</b>\n"
        "• Автовідповідач — у Pro\n"
        "• <code>.send</code> — масова розсилка, у Pro\n"
        "• Більший ліміт <code>.check</code> (10/міс) — у Pro",
        "❓ <b>Помощь — ваш тариф: Standard</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Ваши команды</b>\n"
        "{how}\n"
        "• <code>.info</code> — сводка по собеседнику или чату + аватарка\n"
        "• <code>.me</code> — отправить свою визитку\n"
        "• <code>.ban</code> — в личных: заблокировать и удалить; в группе: выйти\n"
        "• <code>.check</code> — <b>5 проверок в месяц</b>, счётчик обновляется 1-го числа\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Не входит в Standard</b>\n"
        "• Автоответчик — в Pro\n"
        "• <code>.send</code> — массовая рассылка, в Pro\n"
        "• Больший лимит <code>.check</code> (10/мес) — в Pro",
        "❓ <b>Help — your plan: Standard</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Your commands</b>\n"
        "{how}\n"
        "• <code>.info</code> — summary of the person or chat + their avatar\n"
        "• <code>.me</code> — send your own card\n"
        "• <code>.ban</code> — in DMs: block and delete; in a group: leave\n"
        "• <code>.check</code> — <b>5 lookups per month</b>, resets on the 1st\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Not included in Standard</b>\n"
        "• Autoresponder — Pro\n"
        "• <code>.send</code> — bulk send, Pro\n"
        "• A bigger <code>.check</code> allowance (10/mo) — Pro",
    ),
    "help_pro": (
        "❓ <b>Довідка — ваш тариф: Pro</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Ваші команди</b>\n"
        "{how}\n"
        "• <code>.info</code> — зводка по співрозмовнику або чату + аватарка\n"
        "• <code>.me</code> — надіслати свою візитку\n"
        "• <code>.ban</code> — в особистих: заблокувати й видалити; у групі: вийти\n"
        "• <code>.check</code> — <b>10 перевірок на місяць</b>, лічильник оновлюється 1-го числа\n"
        "• <code>.send N текст</code> — <b>до 50 копій за раз, раз на 10 хвилин</b>. Якщо Telegram тимчасово обмежить надсилання, бот зупиниться і напише, скільки встиг.\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Автовідповідач</b>\n"
        "Автоматична відповідь на вхідні, поки вас немає: текст, фото, кнопки-посилання, часове вікно й винятки — усе в ⚙️ Налаштування. Кожне спрацювання дублюється вам у менеджер-бот.",
        "❓ <b>Помощь — ваш тариф: Pro</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Ваши команды</b>\n"
        "{how}\n"
        "• <code>.info</code> — сводка по собеседнику или чату + аватарка\n"
        "• <code>.me</code> — отправить свою визитку\n"
        "• <code>.ban</code> — в личных: заблокировать и удалить; в группе: выйти\n"
        "• <code>.check</code> — <b>10 проверок в месяц</b>, счётчик обновляется 1-го числа\n"
        "• <code>.send N текст</code> — <b>до 50 копий за раз, раз в 10 минут</b>. Если Telegram временно ограничит отправку, бот остановится и напишет, сколько успел.\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Автоответчик</b>\n"
        "Автоматический ответ на входящие, пока вас нет: текст, фото, кнопки-ссылки, временное окно и исключения — всё в ⚙️ Настройки. Каждое срабатывание дублируется вам в менеджер-бот.",
        "❓ <b>Help — your plan: Pro</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Your commands</b>\n"
        "{how}\n"
        "• <code>.info</code> — summary of the person or chat + their avatar\n"
        "• <code>.me</code> — send your own card\n"
        "• <code>.ban</code> — in DMs: block and delete; in a group: leave\n"
        "• <code>.check</code> — <b>10 lookups per month</b>, resets on the 1st\n"
        "• <code>.send N text</code> — <b>up to 50 copies at a time, once every 10 minutes</b>. If Telegram throttles sending, the bot stops and tells you how many went out.\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Autoresponder</b>\n"
        "Replies to incoming messages while you're away: text, photo, link buttons, a time window and exceptions — all in ⚙️ Settings. Every reply is also copied to your manager bot.",
    ),
    "help_premium": (
        "❓ <b>Довідка — ваш тариф: Premium</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Ваші команди</b>\n"
        "{how}\n"
        "• <code>.info</code> — зводка по співрозмовнику або чату + аватарка\n"
        "• <code>.me</code> — надіслати свою візитку\n"
        "• <code>.ban</code> — в особистих: заблокувати й видалити; у групі: вийти\n"
        "• <code>.check</code> — <b>30 перевірок на місяць</b>, лічильник оновлюється 1-го числа\n"
        "• <code>.send N текст</code> — <b>до 100 копій за раз, раз на 2 хвилини</b>. Якщо Telegram тимчасово обмежить надсилання, бот зупиниться і напише, скільки встиг.\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Автовідповідач</b>\n"
        "Автоматична відповідь на вхідні, поки вас немає: текст, фото, кнопки-посилання, часове вікно й винятки — усе в ⚙️ Налаштування. Кожне спрацювання дублюється вам у менеджер-бот.\n"
        "\n"
        "Premium — найповніший тариф: усе з Pro, але втричі більший ліміт <code>.check</code>, удвічі більша розсилка і вп'ятеро коротший інтервал між нею.",
        "❓ <b>Помощь — ваш тариф: Premium</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Ваши команды</b>\n"
        "{how}\n"
        "• <code>.info</code> — сводка по собеседнику или чату + аватарка\n"
        "• <code>.me</code> — отправить свою визитку\n"
        "• <code>.ban</code> — в личных: заблокировать и удалить; в группе: выйти\n"
        "• <code>.check</code> — <b>30 проверок в месяц</b>, счётчик обновляется 1-го числа\n"
        "• <code>.send N текст</code> — <b>до 100 копий за раз, раз в 2 минуты</b>. Если Telegram временно ограничит отправку, бот остановится и напишет, сколько успел.\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Автоответчик</b>\n"
        "Автоматический ответ на входящие, пока вас нет: текст, фото, кнопки-ссылки, временное окно и исключения — всё в ⚙️ Настройки. Каждое срабатывание дублируется вам в менеджер-бот.\n"
        "\n"
        "Premium — самый полный тариф: всё из Pro, но втрое больший лимит <code>.check</code>, вдвое большая рассылка и в пять раз более короткий интервал между ней.",
        "❓ <b>Help — your plan: Premium</b>\n"
        "\n"
        "{bot}\n"
        "\n"
        "{manager}\n"
        "\n"
        "<b>Your commands</b>\n"
        "{how}\n"
        "• <code>.info</code> — summary of the person or chat + their avatar\n"
        "• <code>.me</code> — send your own card\n"
        "• <code>.ban</code> — in DMs: block and delete; in a group: leave\n"
        "• <code>.check</code> — <b>30 lookups per month</b>, resets on the 1st\n"
        "• <code>.send N text</code> — <b>up to 100 copies at a time, once every 2 minutes</b>. If Telegram throttles sending, the bot stops and tells you how many went out.\n"
        "\n"
        "{autosave}\n"
        "\n"
        "<b>Autoresponder</b>\n"
        "Replies to incoming messages while you're away: text, photo, link buttons, a time window and exceptions — all in ⚙️ Settings. Every reply is also copied to your manager bot.\n"
        "\n"
        "Premium is the fullest plan: everything in Pro, with three times the <code>.check</code> allowance, twice the bulk-send size and a five times shorter gap between sends.",
    ),
    "help_btn_all": (
        "📖 Показати весь функціонал",
        "📖 Показать весь функционал",
        "📖 Show everything",
    ),
    "help_btn_mine": (
        "🎯 Функціонал моєї підписки",
        "🎯 Функционал моей подписки",
        "🎯 What my plan includes",
    ),
    "support_text": (
        "🆘 <b>Підтримка</b>\n\nЗ питань — {contact}",
        "🆘 <b>Поддержка</b>\n\nПо вопросам — {contact}",
        "🆘 <b>Support</b>\n\nContact — {contact}",
    ),
    # Stand-in for the manager bot's @username when MANAGER_BOT_USERNAME
    # isn't configured — the hint still has to name it in the user's language.
    "me_link_invalid": (
        "❌ Посилання має починатися з http://, https:// або tg://\n"
        "Спробуйте ще раз або надішліть «-», щоб прибрати.",
        "❌ Ссылка должна начинаться с http://, https:// или tg://\n"
        "Попробуйте ещё раз или отправьте «-», чтобы убрать.",
        "❌ A link must start with http://, https:// or tg://\n"
        "Try again, or send «-» to remove it.",
    ),
    "manager_bot_generic": ("менеджер-бот", "менеджер-бот", "the manager bot"),
    "start_manager_hint": (
        "❗️ <b>Обов'язковий останній крок</b>\n\nЗапустіть менеджер-бота {manager} — саме туди приходитимуть усі сповіщення (видалені/змінені повідомлення, автовідповідач тощо). Без цього кроку сповіщень не буде.",
        "❗️ <b>Обязательный последний шаг</b>\n\nЗапустите менеджер-бота {manager} — именно туда будут приходить все уведомления (удалённые/изменённые сообщения, автоответчик и т.д.). Без этого шага уведомлений не будет.",
        "❗️ <b>One last required step</b>\n\nStart the manager bot {manager} — that's where all notifications arrive (deleted/edited messages, autoresponder, etc). Without this step you won't get any.",
    ),
    "start_manager_btn": ("🔔 Відкрити менеджер-бота", "🔔 Открыть менеджер-бота", "🔔 Open the manager bot"),
    # connect
    "connect_ask_phone": (
        "📱 Щоб підключити акаунт, надішліть свій номер телефону кнопкою нижче.",
        "📱 Чтобы подключить аккаунт, отправьте свой номер телефона кнопкой ниже.",
        "📱 To connect your account, share your phone number with the button below.",
    ),
    "connect_phone_button": ("Надіслати номер 📱", "Отправить номер 📱", "Share number 📱"),
    "connect_bad_phone": (
        "❌ Не вдалося розпізнати номер. Скористайтеся кнопкою нижче.",
        "❌ Не удалось распознать номер. Используйте кнопку ниже.",
        "❌ Couldn't read the number. Use the button below.",
    ),
    "connect_miniapp_button": ("🔗 Продовжити підключення", "🔗 Продолжить подключение", "🔗 Continue connecting"),
    "connect_miniapp_intro": (
        "Натисніть кнопку нижче — вхід у Telegram відбудеться прямо тут, у застосунку.\n\n"
        "⏱ Посилання дійсне 15 хвилин. Не встигли — просто натисніть «Прив'язати акаунт» ще раз.",
        "Нажмите кнопку ниже — вход в Telegram произойдёт прямо здесь, в приложении.\n\n"
        "⏱ Ссылка действительна 15 минут. Не успели — просто нажмите «Привязать аккаунт» ещё раз.",
        "Tap the button below — you'll sign in to Telegram right inside the app.\n\n"
        "⏱ This link is valid for 15 minutes. If it expires, just tap «Link account» again.",
    ),
    "connect_rate_limited": (
        "⏳ Зачекайте хвилину перед повторною спробою.",
        "⏳ Подождите минуту перед повторной попыткой.",
        "⏳ Please wait a minute before trying again.",
    ),
    "connect_already_active": (
        "⏳ Ви вже підключаєте акаунт.\n\n"
        "Скористайтеся кнопкою «Продовжити підключення» вище — вона ще діє {minutes} хв. "
        "Якщо хочете почати заново, дочекайтеся, поки цей час мине.",
        "⏳ Вы уже подключаете аккаунт.\n\n"
        "Используйте кнопку «Продолжить подключение» выше — она действует ещё {minutes} мин. "
        "Если хотите начать заново, дождитесь, пока это время истечёт.",
        "⏳ You already have a connection in progress.\n\n"
        "Use the «Continue connecting» button above — it's valid for {minutes} more min. "
        "To start over, wait for that time to run out.",
    ),
    "connect_expired_notice": (
        "⏱ <b>Час вийшов</b>\n\n"
        "Посилання для підключення акаунта більше не діє. "
        "Спробувати ще раз можна в налаштуваннях.",
        "⏱ <b>Время вышло</b>\n\n"
        "Ссылка для подключения аккаунта больше не действует. "
        "Попробовать ещё раз можно в настройках.",
        "⏱ <b>Time's up</b>\n\n"
        "The account connection link is no longer valid. "
        "You can try again from settings.",
    ),
    "connect_expired_btn": (
        "⚙️ Відкрити налаштування",
        "⚙️ Открыть настройки",
        "⚙️ Open settings",
    ),
    "connect_working": (
        "⏳ Підключаємось до Telegram, зачекайте кілька секунд…",
        "⏳ Подключаемся к Telegram, подождите несколько секунд…",
        "⏳ Connecting to Telegram, this can take a few seconds…",
    ),
    "connect_checking_code": (
        "⏳ Перевіряємо код…",
        "⏳ Проверяем код…",
        "⏳ Checking the code…",
    ),
    "connect_checking_password": (
        "⏳ Перевіряємо пароль…",
        "⏳ Проверяем пароль…",
        "⏳ Checking the password…",
    ),
    "code_sent": (
        "💬 Код надіслано в Telegram.\nВведіть його кнопками:",
        "💬 Код отправлен в Telegram.\nВведите его кнопками:",
        "💬 A code was sent in Telegram.\nEnter it with the buttons:",
    ),
    "code_label": ("💬 Код: {masked}", "💬 Код: {masked}", "💬 Code: {masked}"),
    "btn_resend_sms": (
        "🔄 Код не прийшов? Спробувати ще раз",
        "🔄 Код не пришёл? Попробовать ещё раз",
        "🔄 Code didn't arrive? Try resending",
    ),
    "sms_resent": (
        "🔄 Telegram надіслав код повторно. Канал доставки визначає сам Telegram "
        "(це може знову бути повідомлення в самому Telegram). Введіть новий код кнопками:",
        "🔄 Telegram отправил код повторно. Канал доставки определяет сам Telegram "
        "(это может снова быть сообщение в самом Telegram). Введите новый код кнопками:",
        "🔄 Telegram resent the code. It decides the delivery channel "
        "(it may again be a Telegram in-app message). Enter the new code with the buttons:",
    ),
    "sms_resend_error": (
        "❌ Не вдалося надіслати код повторно. Спробуйте ще раз пізніше.",
        "❌ Не удалось отправить код повторно. Попробуйте ещё раз позже.",
        "❌ Couldn't resend the code. Try again later.",
    ),
    "sms_no_more_options": (
        "❌ Немає запасного каналу для цього акаунту. Код лише в чаті «Telegram» — "
        "перевір усі свої пристрої/сесії.",
        "❌ Нет запасного канала для этого аккаунта. Код только в чате «Telegram» — "
        "проверь все свои устройства/сессии.",
        "❌ No fallback channel for this account. The code only arrives in the "
        "\"Telegram\" chat — check all your devices/sessions.",
    ),
    "code_too_short": ("Код закороткий!", "Код слишком короткий!", "Code is too short!"),
    "code_must_use_keyboard": (
        "⚠️ Не вводьте код текстом — Telegram може заблокувати його через антифрод-систему. "
        "Введіть код кнопками на клавіатурі нижче.",
        "⚠️ Не вводите код текстом — Telegram может заблокировать его через антифрод-систему. "
        "Введите код кнопками на клавиатуре ниже.",
        "⚠️ Don't type the code as text — Telegram may block it via its anti-fraud system. "
        "Enter it using the keyboard buttons below.",
    ),
    "enter_2fa": (
        "🔐 Увімкнено 2FA. Надішліть пароль повідомленням:",
        "🔐 Включена 2FA. Отправьте пароль сообщением:",
        "🔐 2FA is on. Send your password as a message:",
    ),
    "wrong_password": (
        "❌ Невірний пароль. Спробуйте ще раз:",
        "❌ Неверный пароль. Попробуйте ещё раз:",
        "❌ Wrong password. Try again:",
    ),
    "invalid_code": (
        "❌ Невірний код. Введіть ще раз:",
        "❌ Неверный код. Введите ещё раз:",
        "❌ Wrong code. Enter it again:",
    ),
    "connect_error": (
        "❌ Сталася помилка під час підключення. Спробуйте ще раз.",
        "❌ Произошла ошибка при подключении. Попробуйте ещё раз.",
        "❌ Something went wrong while connecting. Try again.",
    ),
    "connect_success": (
        "✅ Акаунт підключено!",
        "✅ Аккаунт подключён!",
        "✅ Account connected!",
    ),
    # subscribe
    "sub_choose_duration": ("Оберіть тривалість підписки:", "Выберите длительность подписки:", "Choose a subscription duration:"),
    "sub_duration_month": ("Місяць", "Месяц", "Month"),
    "sub_duration_3months": ("3 місяці", "3 месяца", "3 months"),
    "sub_duration_year": ("Рік", "Год", "Year"),
    "sub_discount_applied": (
        "🎁 Знижка {pct}% за реферальним посиланням застосована.",
        "🎁 Скидка {pct}% по реферальной ссылке применена.",
        "🎁 A {pct}% referral discount has been applied.",
    ),
    "sub_buy": ("💳 Купити", "💳 Купить", "💳 Buy"),
    "sub_in_dev_btn": ("🚧 в розробці", "🚧 в разработке", "🚧 in development"),
    "sub_back": ("◀️ Назад", "◀️ Назад", "◀️ Back"),
    "sub_in_dev_alert": ("Цей тариф ще в розробці 🚧", "Этот тариф ещё в разработке 🚧", "This tariff is still in development 🚧"),
    "sub_unavailable": ("Недоступно", "Недоступно", "Unavailable"),
    "sub_crypto_unavailable": ("Криптооплата недоступна", "Криптооплата недоступна", "Crypto payment is unavailable"),
    "sub_invoice_error": ("Помилка створення інвойсу", "Ошибка создания инвойса", "Failed to create the invoice"),
    "sub_pay_btn": ("💎 Сплатити", "💎 Оплатить", "💎 Pay"),
    "sub_check_btn": ("✅ Перевірити оплату", "✅ Проверить оплату", "✅ Check payment"),
    "sub_crypto_prompt": (
        "💎 Оплата тарифу <b>{title}</b> ({amount} USDT).\nСплатіть за посиланням і натисніть «Перевірити оплату».",
        "💎 Оплата тарифа <b>{title}</b> ({amount} USDT).\nОплатите по ссылке и нажмите «Проверить оплату».",
        "💎 Payment for <b>{title}</b> ({amount} USDT).\nPay via the link and tap «Check payment».",
    ),
    "sub_not_paid": ("Оплату ще не отримано ⏳", "Оплата ещё не получена ⏳", "Payment not received yet ⏳"),
    "sub_check_error": ("Помилка перевірки", "Ошибка проверки", "Check failed"),
    "sub_no_invoice": ("Не знайдено інвойс", "Инвойс не найден", "Invoice not found"),
    "sub_success": (
        "✅ Оплату отримано. Тариф <b>{title}</b> активовано!",
        "✅ Оплата получена. Тариф <b>{title}</b> активирован!",
        "✅ Payment received. Tariff <b>{title}</b> is active!",
    ),
    "sub_ready": (
        "Усе готово — тариф уже діє.",
        "Всё готово — тариф уже действует.",
        "All set — your plan is live.",
    ),
    "sub_connect_hint": (
        "Підключіть акаунт: ⚙️ Налаштування → 🔗 Прив'язати акаунт.",
        "Подключите аккаунт: ⚙️ Настройки → 🔗 Привязать аккаунт.",
        "Connect your account: ⚙️ Settings → 🔗 Link account.",
    ),
    "sub_stars_title": ("Підписка {title}", "Подписка {title}", "{title} subscription"),
    "sub_stars_desc": ("Тариф {title} на {days} днів", "Тариф {title} на {days} дней", "{title} tariff for {days} days"),
    # --- card payments (WayForPay, UAH) ---
    "sub_card_unavailable": (
        "Оплата карткою тимчасово недоступна",
        "Оплата картой временно недоступна",
        "Card payment is temporarily unavailable",
    ),
    "sub_pay_card_btn": ("💳 Сплатити карткою", "💳 Оплатить картой", "💳 Pay by card"),
    "sub_card_prompt": (
        "💳 Оплата тарифу <b>{title}</b> — {amount} ₴.\n"
        "Натисніть кнопку нижче, щоб перейти на захищену сторінку оплати.\n\n"
        "🔄 Підписка продовжуватиметься автоматично кожні {days} днів. "
        "Скасувати можна будь-коли в ⚙️ Налаштуваннях.",
        "💳 Оплата тарифа <b>{title}</b> — {amount} ₴.\n"
        "Нажмите кнопку ниже, чтобы перейти на защищённую страницу оплаты.\n\n"
        "🔄 Подписка будет продлеваться автоматически каждые {days} дней. "
        "Отменить можно в любой момент в ⚙️ Настройках.",
        "💳 Payment for <b>{title}</b> — {amount} UAH.\n"
        "Tap the button below to open the secure payment page.\n\n"
        "🔄 The subscription renews automatically every {days} days. "
        "You can cancel any time in ⚙️ Settings.",
    ),
    "sub_card_prompt_once": (
        "💳 Оплата тарифу <b>{title}</b> — {amount} ₴.\n"
        "Натисніть кнопку нижче, щоб перейти на захищену сторінку оплати.\n\n"
        "Це разовий платіж — автоматичного продовження не буде.",
        "💳 Оплата тарифа <b>{title}</b> — {amount} ₴.\n"
        "Нажмите кнопку ниже, чтобы перейти на защищённую страницу оплаты.\n\n"
        "Это разовый платёж — автоматического продления не будет.",
        "💳 Payment for <b>{title}</b> — {amount} UAH.\n"
        "Tap the button below to open the secure payment page.\n\n"
        "This is a one-off charge — it will not renew automatically.",
    ),
    # --- auto-renewal, managed from settings ---
    "set_autorenew_on": (
        "🔄 Автопродовження: увімкнено",
        "🔄 Автопродление: включено",
        "🔄 Auto-renewal: on",
    ),
    "set_autorenew_btn_cancel": (
        "🔄 Скасувати автопродовження",
        "🔄 Отменить автопродление",
        "🔄 Cancel auto-renewal",
    ),
    "set_autorenew_cancelled": (
        "🔄 Автопродовження скасовано. Підписка діятиме до {date}, далі списань не буде.",
        "🔄 Автопродление отменено. Подписка будет действовать до {date}, дальше списаний не будет.",
        "🔄 Auto-renewal cancelled. The subscription stays active until {date}, with no further charges.",
    ),
    "set_autorenew_cancel_failed": (
        "Не вдалося скасувати автопродовження. Спробуйте ще раз або напишіть у підтримку.",
        "Не удалось отменить автопродление. Попробуйте ещё раз или напишите в поддержку.",
        "Could not cancel auto-renewal. Try again or contact support.",
    ),
    # --- the payment page itself (pay_web) ---
    "pay_title": ("Оплата підписки", "Оплата подписки", "Subscription payment"),
    "pay_product": (
        "Тариф {title} — {days} днів",
        "Тариф {title} — {days} дней",
        "{title} tariff — {days} days",
    ),
    "pay_button": ("Сплатити карткою", "Оплатить картой", "Pay by card"),
    "pay_autorenew_note": (
        "Підписка продовжуватиметься автоматично. Скасувати можна будь-коли в боті.",
        "Подписка будет продлеваться автоматически. Отменить можно в любой момент в боте.",
        "The subscription renews automatically. You can cancel any time in the bot.",
    ),
    "pay_once_note": (
        "Разовий платіж, без автоматичного продовження.",
        "Разовый платёж, без автоматического продления.",
        "A one-off charge, with no automatic renewal.",
    ),
    "pay_expired_title": ("Посилання застаріло", "Ссылка устарела", "This link is no longer valid"),
    "pay_expired_text": (
        "Поверніться в бот і почніть оплату заново.",
        "Вернитесь в бот и начните оплату заново.",
        "Go back to the bot and start the payment again.",
    ),
    "pay_already_title": ("Уже оплачено", "Уже оплачено", "Already paid"),
    "pay_already_text": (
        "Ця підписка вже активна — платити ще раз не потрібно.",
        "Эта подписка уже активна — платить ещё раз не нужно.",
        "This subscription is already active — no need to pay again.",
    ),
    "pay_done_title": ("Дякуємо!", "Спасибо!", "Thank you!"),
    "pay_done_text": (
        "Повертайтеся в бот — підтвердження прийде туди, щойно банк підтвердить платіж.",
        "Возвращайтесь в бот — подтверждение придёт туда, как только банк подтвердит платёж.",
        "Head back to the bot — confirmation arrives there as soon as the bank confirms the payment.",
    ),
    "pay_refunded": (
        "↩️ Кошти за тариф <b>{title}</b> повернуто, підписку зупинено.\n"
        "Якщо це помилка — напишіть у підтримку.",
        "↩️ Средства за тариф <b>{title}</b> возвращены, подписка остановлена.\n"
        "Если это ошибка — напишите в поддержку.",
        "↩️ Your payment for <b>{title}</b> was refunded and the subscription has stopped.\n"
        "If that looks wrong, contact support.",
    ),
    "pay_renew_failed": (
        "⚠️ Не вдалося продовжити тариф <b>{title}</b> — банк відхилив списання.\n"
        "Спробу буде повторено завтра. Перевірте баланс або оплатіть вручну: 💳 Тарифи.",
        "⚠️ Не удалось продлить тариф <b>{title}</b> — банк отклонил списание.\n"
        "Попытка повторится завтра. Проверьте баланс или оплатите вручную: 💳 Тарифы.",
        "⚠️ Could not renew <b>{title}</b> — the bank declined the charge.\n"
        "It will be retried tomorrow. Check your balance or pay manually: 💳 Plans.",
    ),
    # settings — profile
    "set_title": ("⚙️ <b>Налаштування</b>", "⚙️ <b>Настройки</b>", "⚙️ <b>Settings</b>"),
    "set_joined": ("📅 В боті з: {date}", "📅 В боте с: {date}", "📅 Joined: {date}"),
    "set_username": ("👤 Юзернейм: {uname}", "👤 Юзернейм: {uname}", "👤 Username: {uname}"),
    "set_sub": ("💳 Підписка: {sub}", "💳 Подписка: {sub}", "💳 Subscription: {sub}"),
    "set_sub_active": ("{title} (активна)", "{title} (активна)", "{title} (active)"),
    "set_sub_none": ("немає", "нет", "none"),
    "set_account": ("🔌 Акаунт: {conn}", "🔌 Аккаунт: {conn}", "🔌 Account: {conn}"),
    "set_check_quota": (
        "🔎 Кількість запитів (.check): {left}/{total}",
        "🔎 Кол-во запросов (.check): {left}/{total}",
        "🔎 Requests (.check): {left}/{total}",
    ),
    "set_btn_me": ("🪪 Візитка (.me)", "🪪 Визитка (.me)", "🪪 Card (.me)"),
    "set_btn_ar": ("🤖 Автовідповідач (Pro)", "🤖 Автоответчик (Pro)", "🤖 Autoresponder (Pro)"),
    "set_btn_ignored": ("🚫 Ігноровані чати", "🚫 Игнорируемые чаты", "🚫 Ignored chats"),
    "set_btn_lang": ("🌐 Мова", "🌐 Язык", "🌐 Language"),
    "set_btn_features": ("🎛 Функції", "🎛 Функции", "🎛 Features"),
    "features_title": (
        "🎛 Увімкни або вимкни окремі функції userbot-а.",
        "🎛 Включи или выключи отдельные функции юзербота.",
        "🎛 Turn individual userbot features on or off.",
    ),
    "feat_deleted": ("🗑 Видалені повідомлення", "🗑 Удалённые сообщения", "🗑 Deleted messages"),
    "feat_edited": ("✏️ Змінені повідомлення", "✏️ Изменённые сообщения", "✏️ Edited messages"),
    "feat_info": (".info команда", ".info команда", ".info command"),
    "feat_me": (".me — візитка", ".me — визитка", ".me — card"),
    "feat_ban": (".ban — блок/бан", ".ban — блок/бан", ".ban — block/ban"),
    "feat_check": (".check — перевірка", ".check — проверка", ".check — lookup"),
    "feat_viewonce_photo": ("👁 Одноразові фото", "👁 Одноразовые фото", "👁 One-time photos"),
    "feat_viewonce_voice": ("🎙 Одноразові голосові", "🎙 Одноразовые голосовые", "🎙 One-time voice"),
    "lang_title": (
        "🌐 Оберіть мову інтерфейсу.\n\n<i>Telegram не завжди коректно передає мову боту — оберіть вручну.</i>",
        "🌐 Выберите язык интерфейса.\n\n<i>Telegram не всегда корректно передаёт язык боту — выберите вручную.</i>",
        "🌐 Choose the interface language.\n\n<i>Telegram doesn't always report your language correctly — pick it manually.</i>",
    ),
    "lang_uk": ("🇺🇦 Українська", "🇺🇦 Українська", "🇺🇦 Українська"),
    "lang_ru": ("🇷🇺 Русский", "🇷🇺 Русский", "🇷🇺 Русский"),
    "lang_en": ("🇬🇧 English", "🇬🇧 English", "🇬🇧 English"),
    "lang_saved": ("✅ Мову збережено.", "✅ Язык сохранён.", "✅ Language saved."),
    "set_btn_unlink": ("❌ Відв'язати акаунт", "❌ Отвязать аккаунт", "❌ Unlink account"),
    "set_btn_link": ("🔗 Прив'язати акаунт", "🔗 Привязать аккаунт", "🔗 Link account"),
    "set_me_title": ("🪪 Візитка .me:", "🪪 Визитка .me:", "🪪 .me card:"),
    "set_ar_title": (
        "🤖 Автовідповідач (працює на тарифі Pro):",
        "🤖 Автоответчик (работает на тарифе Pro):",
        "🤖 Autoresponder (works on the Pro tariff):",
    ),
    "me_btn_text": ("✏️ Текст", "✏️ Текст", "✏️ Text"),
    "me_btn_link": ("🔗 Посилання", "🔗 Ссылка", "🔗 Link"),
    "me_btn_photo": ("🖼 Фото", "🖼 Фото", "🖼 Photo"),
    "me_btn_buttons": ("🔘 Кнопки", "🔘 Кнопки", "🔘 Buttons"),
    "me_btn_view": ("👁 Перегляд", "👁 Просмотр", "👁 Preview"),
    "me_btn_clear": ("🗑 Очистити", "🗑 Очистить", "🗑 Clear"),
    "btn_back": ("⬅️ Назад", "⬅️ Назад", "⬅️ Back"),
    "ar_btn_toggle": ("🔀 Увімк/Вимк", "🔀 Вкл/Выкл", "🔀 On/Off"),
    "ar_btn_msg": ("✏️ Повідомлення", "✏️ Сообщение", "✏️ Message"),
    "ar_btn_time": ("🕒 Час", "🕒 Время", "🕒 Time"),
    "ar_btn_exc": ("🚫 Винятки", "🚫 Исключения", "🚫 Exceptions"),
    "prompt_me_text": ("Надішліть текст візитки:", "Отправьте текст визитки:", "Send the card text:"),
    "prompt_me_link": (
        "Надішліть посилання (або `-` щоб прибрати):",
        "Отправьте ссылку (или `-` чтобы убрать):",
        "Send a link (or `-` to remove):",
    ),
    "prompt_me_photo": ("Надішліть фото для візитки:", "Отправьте фото для визитки:", "Send a photo for the card:"),
    "prompt_me_buttons": (
        "Кнопки, по одній на рядок: `Назва | https://посилання`:",
        "Кнопки, по одной на строку: `Название | https://ссылка`:",
        "Buttons, one per line: `Label | https://link`:",
    ),
    "prompt_ar_msg": ("Надішліть текст автовідповіді:", "Отправьте текст автоответа:", "Send the autoresponder text:"),
    "prompt_ar_photo": (
        "Надішліть фото для автовідповіді:",
        "Отправьте фото для автоответа:",
        "Send a photo for the autoresponder:",
    ),
    "prompt_ar_time": (
        "Час активності `09:00-18:00` (або `-` цілодобово):",
        "Время активности `09:00-18:00` (или `-` круглосуточно):",
        "Active window `09:00-18:00` (or `-` for 24/7):",
    ),
    "prompt_ar_exc": (
        "ID винятків через пробіл/кому (або `-` щоб очистити):",
        "ID исключений через пробел/запятую (или `-` чтобы очистить):",
        "Exception IDs, space/comma separated (or `-` to clear):",
    ),
    "saved_text": ("✅ Текст збережено.", "✅ Текст сохранён.", "✅ Text saved."),
    "saved_link": ("✅ Посилання збережено.", "✅ Ссылка сохранена.", "✅ Link saved."),
    "saved_photo": ("✅ Фото збережено.", "✅ Фото сохранено.", "✅ Photo saved."),
    "saved_buttons": ("✅ Збережено кнопок: {n}.", "✅ Сохранено кнопок: {n}.", "✅ Buttons saved: {n}."),
    "saved_message": ("✅ Повідомлення збережено.", "✅ Сообщение сохранено.", "✅ Message saved."),
    "ar_toggled": (
        "Автовідповідач {state}",
        "Автоответчик {state}",
        "Autoresponder {state}",
    ),
    "ar_state_on": ("увімкнено", "включён", "on"),
    "ar_state_off": ("вимкнено", "выключен", "off"),
    "saved_time": ("✅ Час активності: {window}.", "✅ Время активности: {window}.", "✅ Active window: {window}."),
    "window_allday": ("цілодобово", "круглосуточно", "24/7"),
    "saved_exc": ("✅ Винятків: {n}.", "✅ Исключений: {n}.", "✅ Exceptions: {n}."),
    "me_cleared": ("Візитку очищено", "Визитка очищена", "Card cleared"),
    "account_unlinked": ("Акаунт відв'язано", "Аккаунт отвязан", "Account unlinked"),
    "ignored_empty": (
        "🚫 <b>Ігноровані чати</b>\n\nСписок порожній — тут з'являться чати, додані кнопкою «Ігнорувати цей чат» під сповіщеннями.",
        "🚫 <b>Игнорируемые чаты</b>\n\nСписок пуст — здесь появятся чаты, добавленные кнопкой «Игнорировать этот чат» под уведомлениями.",
        "🚫 <b>Ignored chats</b>\n\nEmpty — chats added via the «Ignore this chat» button under notifications will appear here.",
    ),
    "ignored_header": (
        "🚫 <b>Ігноровані чати</b>\n\nНатисніть, щоб прибрати зі списку:",
        "🚫 <b>Игнорируемые чаты</b>\n\nНажмите, чтобы убрать из списка:",
        "🚫 <b>Ignored chats</b>\n\nTap to remove from the list:",
    ),
    "ignored_removed": ("Прибрано зі списку", "Убрано из списка", "Removed from the list"),
    "preview_empty": ("Візитка порожня.", "Визитка пуста.", "Card is empty."),
    "preview_no_text": ("(без тексту)", "(без текста)", "(no text)"),
    # userbot command responses (owner's language)
    "session_invalid": (
        "⚠️ Ваш акаунт відключено — сесія більше недійсна.\n\n"
        "Таке буває після виходу з акаунта на іншому пристрої, зміни пароля або "
        "завершення сеансу вручну. Щоб бот працював далі, прив'яжіть акаунт заново.",
        "⚠️ Ваш аккаунт отключён — сессия больше недействительна.\n\n"
        "Такое бывает после выхода из аккаунта на другом устройстве, смены пароля или "
        "завершения сеанса вручную. Чтобы бот работал дальше, привяжите аккаунт заново.",
        "⚠️ Your account was disconnected — the session is no longer valid.\n\n"
        "This happens after logging out on another device, changing your password, or "
        "ending the session manually. Re-link your account to keep the bot working.",
    ),
    "btn_relink_account": (
        "🔗 Прив'язати акаунт заново",
        "🔗 Привязать аккаунт заново",
        "🔗 Re-link account",
    ),
    "ub_no_sub": (
        "❌ Немає активної підписки. Оформіть у керуючому боті.",
        "❌ Нет активной подписки. Оформите в управляющем боте.",
        "❌ No active subscription. Get one in the manager bot.",
    ),
    "ub_tariff_excludes": (
        "❌ Ваш тариф не включає цю команду.",
        "❌ Ваш тариф не включает эту команду.",
        "❌ Your tariff doesn't include this command.",
    ),
    "ub_feature_disabled": (
        "🎛 Цю функцію вимкнено в налаштуваннях бота.",
        "🎛 Эта функция выключена в настройках бота.",
        "🎛 This feature is turned off in the bot settings.",
    ),
    "ub_me_not_set": (
        "ℹ️ Візитку ще не налаштовано. Зробіть це в керуючому боті: /settings",
        "ℹ️ Визитка ещё не настроена. Сделайте это в управляющем боте: /settings",
        "ℹ️ Card isn't set up yet. Do it in the manager bot: /settings",
    ),
    "ub_info_error": ("⚠️ Не вдалося отримати інформацію.", "⚠️ Не удалось получить информацию.", "⚠️ Couldn't fetch info."),
    "ub_ban_error": ("⚠️ Не вдалося виконати бан.", "⚠️ Не удалось выполнить бан.", "⚠️ Ban failed."),
    "ub_check_limit": (
        "❌ Ліміт .check вичерпано ({used}/{quota}). Оновиться наступного місяця.",
        "❌ Лимит .check исчерпан ({used}/{quota}). Обновится в следующем месяце.",
        "❌ .check limit reached ({used}/{quota}). Resets next month.",
    ),
    "ub_send_bad_count": (
        "❌ Кількість має бути від 1 до {max}: .send 10 текст",
        "❌ Количество должно быть от 1 до {max}: .send 10 текст",
        "❌ Count must be between 1 and {max}: .send 10 text",
    ),
    "ub_send_cooldown": (
        "⏳ Зачекайте ще {minutes} хв перед наступним .send.",
        "⏳ Подождите ещё {minutes} мин перед следующим .send.",
        "⏳ Wait {minutes} more min before the next .send.",
    ),
    "ub_send_flood_stopped": (
        "⚠️ Telegram тимчасово обмежив надсилання. Надіслано {sent}/{total}.",
        "⚠️ Telegram временно ограничил отправку. Отправлено {sent}/{total}.",
        "⚠️ Telegram temporarily limited sending. Sent {sent}/{total}.",
    ),
    "ub_check_note": (
        "🔎 .check — логіка в розробці. Витрачено {used}/{quota} цього місяця.",
        "🔎 .check — логика в разработке. Потрачено {used}/{quota} в этом месяце.",
        "🔎 .check — logic in development. Used {used}/{quota} this month.",
    ),
    # userbot notifications (owner's language)
    "note_deleted": ("🗑 <b>Видалене повідомлення</b>", "🗑 <b>Удалённое сообщение</b>", "🗑 <b>Deleted message</b>"),
    "note_edited": ("✏️ <b>Змінене повідомлення</b>", "✏️ <b>Изменённое сообщение</b>", "✏️ <b>Edited message</b>"),
    "note_chat": ("Чат", "Чат", "Chat"),
    "note_from": ("Від", "От", "From"),
    "note_was": ("Було", "Было", "Was"),
    "note_now": ("Стало", "Стало", "Now"),
    "note_ar_fired": ("🤖 <b>Автовідповідач спрацював</b>", "🤖 <b>Автоответчик сработал</b>", "🤖 <b>Autoresponder fired</b>"),
    "note_incoming": ("Вхідне", "Входящее", "Incoming"),
    "note_view_once": ("👁 <b>Одноразове {kind}</b>", "👁 <b>Одноразовое {kind}</b>", "👁 <b>One-time {kind}</b>"),
    "kind_photo": ("фото", "фото", "photo"),
    "kind_voice": ("голосове", "голосовое", "voice"),
    "kind_video": ("відео", "видео", "video"),
    "kind_media": ("медіа", "медиа", "media"),
    "kind_document": ("файл", "файл", "file"),
    "kind_location": ("локація", "локация", "location"),
    "btn_ignore_chat": ("🚫 Ігнорувати цей чат", "🚫 Игнорировать этот чат", "🚫 Ignore this chat"),
    "unknown": ("невідомо", "неизвестно", "unknown"),
    # manager bot
    "manager_greeting": (
        "👋 Це ваш персональний менеджер-бот.\n\nСюди приходитимуть: видалені/змінені повідомлення, "
        "спрацювання автовідповідача, результати .info/.check, одноразові фото/гс.\n\n"
        "Нічого налаштовувати не треба — просто тримайте цей чат відкритим.",
        "👋 Это ваш персональный менеджер-бот.\n\nСюда будут приходить: удалённые/изменённые сообщения, "
        "срабатывания автоответчика, результаты .info/.check, одноразовые фото/гс.\n\n"
        "Ничего настраивать не нужно — просто держите этот чат открытым.",
        "👋 This is your personal manager bot.\n\nYou'll receive here: deleted/edited messages, "
        "autoresponder hits, .info/.check results, one-time photos/voice.\n\n"
        "Nothing to configure — just keep this chat open.",
    ),
    "ignore_added": ("🚫 Чат додано в ігнор-лист.", "🚫 Чат добавлен в игнор-лист.", "🚫 Chat added to the ignore list."),
    "ignore_user_error": (
        "Помилка: користувача не знайдено.",
        "Ошибка: пользователь не найден.",
        "Error: user not found.",
    ),
    # blocked account
    "account_blocked": (
        "🚫 Ваш акаунт заблоковано адміністратором.",
        "🚫 Ваш аккаунт заблокирован администратором.",
        "🚫 Your account has been blocked by an administrator.",
    ),
    # --- .info output (userbot/formatting.py) ---
    "ub_info_members": ("👥 Учасників: {count}", "👥 Участников: {count}", "👥 Members: {count}"),
    "ub_info_no_username": ("🔗 без юзернейму", "🔗 без юзернейма", "🔗 no username"),
    "ub_info_premium": ("⭐️ Premium", "⭐️ Premium", "⭐️ Premium"),
    "ub_info_no_premium": ("◽️ без Premium", "◽️ без Premium", "◽️ no Premium"),
    "ub_entity_chat": ("чат", "чат", "chat"),
    "ub_entity_user": ("користувач", "пользователь", "user"),
    # --- admin panel (owner-only commands) ---
    "adm_starting": ("🚀 Запускаю адмін-панель…", "🚀 Запускаю админ-панель…", "🚀 Starting the admin panel…"),
    "adm_start_failed": (
        "❌ Не вдалося запустити панель. Дивіться логи.",
        "❌ Не удалось запустить панель. Смотрите логи.",
        "❌ Couldn't start the panel. Check the logs.",
    ),
    "adm_panel_ready": (
        "🛠 <b>Адмін-панель</b>\n\n🔗 {url}\n\nТокен: <code>{token}</code>\nЗупинити — /admin_stop",
        "🛠 <b>Админ-панель</b>\n\n🔗 {url}\n\nТокен: <code>{token}</code>\nОстановить — /admin_stop",
        "🛠 <b>Admin panel</b>\n\n🔗 {url}\n\nToken: <code>{token}</code>\nStop it — /admin_stop",
    ),
    "adm_stopped": ("🛑 Адмін-панель зупинено.", "🛑 Админ-панель остановлена.", "🛑 Admin panel stopped."),
    # --- Mini App connect page (connect_web) ---
    # Served into the page itself, so these carry the inline HTML the layout
    # needs (<br>, <b>). Anything interpolated into them at runtime is
    # escaped first — see connect_web/static/app.js.
    "wa_title": ("Підключення акаунта", "Подключение аккаунта", "Connect your account"),
    "wa_outside_title": ("Відкрийте через бота", "Откройте через бота", "Open this from the bot"),
    "wa_outside_body": (
        "Ця сторінка працює лише всередині Telegram.<br>Поверніться до бота і натисніть «Прив'язати акаунт».",
        "Эта страница работает только внутри Telegram.<br>Вернитесь к боту и нажмите «Привязать аккаунт».",
        "This page only works inside Telegram.<br>Go back to the bot and tap «Link account».",
    ),
    "wa_expired_title": ("Час вийшов", "Время вышло", "Time's up"),
    "wa_expired_body": (
        "Це посилання вже недійсне.<br>Поверніться в бот і натисніть «Прив'язати акаунт» ще раз —<br>це займе хвилину.",
        "Эта ссылка больше недействительна.<br>Вернитесь в бот и нажмите «Привязать аккаунт» ещё раз —<br>это займёт минуту.",
        "This link is no longer valid.<br>Go back to the bot and tap «Link account» again —<br>it only takes a minute.",
    ),
    "wa_code_title": ("Введіть код", "Введите код", "Enter the code"),
    "wa_code_sending": (
        "Надсилаємо код на <b>{phone}</b>…",
        "Отправляем код на <b>{phone}</b>…",
        "Sending a code to <b>{phone}</b>…",
    ),
    "wa_code_sent": (
        "Ми надіслали код у ваш чат <b>Telegram</b>.",
        "Мы отправили код в ваш чат <b>Telegram</b>.",
        "We sent the code to your <b>Telegram</b> chat.",
    ),
    "wa_code_phone": ("Номер: <b>{phone}</b>", "Номер: <b>{phone}</b>", "Number: <b>{phone}</b>"),
    "wa_code_retry": ("Надіслати код ще раз", "Отправить код ещё раз", "Send the code again"),
    "wa_code_send_failed": (
        "Не вдалося надіслати код: {error}",
        "Не удалось отправить код: {error}",
        "Couldn't send the code: {error}",
    ),
    "wa_code_expired": (
        "Код застарів — запросіть новий.",
        "Код устарел — запросите новый.",
        "The code expired — request a new one.",
    ),
    "wa_code_too_short": ("Код закороткий.", "Код слишком короткий.", "That code is too short."),
    "wa_code_checking": ("Перевіряємо код…", "Проверяем код…", "Checking the code…"),
    "wa_resuming": ("Відновлюємо спробу…", "Восстанавливаем попытку…", "Resuming your attempt…"),
    "wa_error": ("Помилка: {error}", "Ошибка: {error}", "Error: {error}"),
    "wa_server_rejected": ("Сервер відхилив: {error}", "Сервер отклонил: {error}", "The server rejected it: {error}"),
    "wa_2fa_title": (
        "Пароль двоетапної перевірки",
        "Пароль двухэтапной проверки",
        "Two-step verification password",
    ),
    "wa_2fa_body": (
        "Введіть хмарний пароль вашого акаунта.",
        "Введите облачный пароль вашего аккаунта.",
        "Enter your account's cloud password.",
    ),
    "wa_2fa_placeholder": ("Пароль", "Пароль", "Password"),
    "wa_2fa_submit": ("Підтвердити", "Подтвердить", "Confirm"),
    "wa_2fa_checking": ("Перевіряємо пароль…", "Проверяем пароль…", "Checking the password…"),
    "wa_2fa_note": (
        "Пароль перевіряється <b>на вашому пристрої</b> і не надсилається ані нам, ані будь-кому іншому.",
        "Пароль проверяется <b>на вашем устройстве</b> и не отправляется ни нам, ни кому-либо ещё.",
        "Your password is checked <b>on your own device</b> and is never sent to us or anyone else.",
    ),
    "wa_done_title": ("Акаунт підключено", "Аккаунт подключён", "Account connected"),
    "wa_done_body": (
        "Готово — можете закривати це вікно.",
        "Готово — можете закрывать это окно.",
        "All done — you can close this window.",
    ),
}

_FEATURES: dict[str, tuple[list[str], list[str], list[str]]] = {
    "standard": (
        [".info — зводка по юзеру/чату", ".me — власна візитка", ".ban — бан + видалення",
         ".check — 5/місяць", "Автозбереження видалених/змінених повідомлень"],
        [".info — сводка по юзеру/чату", ".me — своя визитка", ".ban — бан + удаление",
         ".check — 5/месяц", "Автосохранение удалённых/изменённых сообщений"],
        [".info — user/chat summary", ".me — your own card", ".ban — ban + delete",
         ".check — 5/month", "Auto-save of deleted/edited messages"],
    ),
    "pro": (
        ["Усе зі Standard", ".check — 10/місяць", "Автовідповідач (налаштовується)", ".send — масове надсилання (до 50 повідомлень, раз на 10 хв)"],
        ["Всё из Standard", ".check — 10/месяц", "Автоответчик (настраивается)", ".send — массовая рассылка (до 50 сообщений, раз в 10 мин)"],
        ["Everything in Standard", ".check — 10/month", "Autoresponder (configurable)", ".send — bulk send (up to 50 messages, once per 10 min)"],
    ),
    "premium": (
        ["Усе з Pro", ".check — 30/місяць", ".send — до 100 повідомлень, раз на 2 хв", "Автовідповідач (налаштовується)"],
        ["Всё из Pro", ".check — 30/месяц", ".send — до 100 сообщений, раз в 2 мин", "Автоответчик (настраивается)"],
        ["Everything in Pro", ".check — 30/month", ".send — up to 100 messages, once per 2 min", "Autoresponder (configurable)"],
    ),
}
