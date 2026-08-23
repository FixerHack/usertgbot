"""Lightweight 3-language i18n (UK / RU / EN), no external deps.

Language is chosen from the Telegram `language_code` (aiogram) / `lang_code`
(Telethon). Strings live here as `(uk, ru, en)` tuples so every key has all
three in one place; `t(lang, key, **kwargs)` formats one. Reply-keyboard
buttons are matched across languages via `variants(key)`.
"""

from __future__ import annotations

_LANG_IDX = {"uk": 0, "ru": 1, "en": 2}
DEFAULT_LANG = "uk"


def resolve_lang(language_code: str | None) -> str:
    """Map a Telegram language code to one of our supported languages."""
    if not language_code:
        return DEFAULT_LANG
    code = language_code[:2].lower()
    if code in ("uk", "ru", "en"):
        return code
    return "en"  # anything else falls back to English


def lang_of(event) -> str:
    """Resolve language from an aiogram Message/CallbackQuery (via from_user)."""
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
    "help_text": (
        "❓ <b>Довідка</b>\n\n"
        "<b>Керуючий бот</b>\n"
        "• 💳 Тарифи — оформити/змінити підписку\n"
        "• ⚙️ Налаштування — візитка .me, автовідповідач, керування акаунтом\n"
        "• 🆘 Підтримка — зв'язок із менеджером\n\n"
        "<b>Команди в переписках</b> (з підключеного акаунта):\n"
        "• <code>.info</code> — зводка по юзеру/чату (+ 1 аватарка)\n"
        "• <code>.me</code> — надіслати свою візитку\n"
        "• <code>.ban</code> — заблокувати й видалити (в чаті — вийти/бан)\n"
        "• <code>.check</code> — перевірка (ліміт за тарифом)\n\n"
        "<b>Менеджер-бот</b> надсилає вам: видалені/змінені повідомлення, "
        "спрацювання автовідповідача, результати .info/.check, одноразові фото/гс.",
        "❓ <b>Помощь</b>\n\n"
        "<b>Управляющий бот</b>\n"
        "• 💳 Тарифы — оформить/изменить подписку\n"
        "• ⚙️ Настройки — визитка .me, автоответчик, управление аккаунтом\n"
        "• 🆘 Поддержка — связь с менеджером\n\n"
        "<b>Команды в переписках</b> (с подключённого аккаунта):\n"
        "• <code>.info</code> — сводка по юзеру/чату (+ 1 аватарка)\n"
        "• <code>.me</code> — отправить свою визитку\n"
        "• <code>.ban</code> — заблокировать и удалить (в чате — выйти/бан)\n"
        "• <code>.check</code> — проверка (лимит по тарифу)\n\n"
        "<b>Менеджер-бот</b> присылает вам: удалённые/изменённые сообщения, "
        "срабатывания автоответчика, результаты .info/.check, одноразовые фото/гс.",
        "❓ <b>Help</b>\n\n"
        "<b>Manager bot</b>\n"
        "• 💳 Plans — buy/change a subscription\n"
        "• ⚙️ Settings — .me card, autoresponder, account management\n"
        "• 🆘 Support — contact the manager\n\n"
        "<b>In-chat commands</b> (from a connected account):\n"
        "• <code>.info</code> — user/chat summary (+ 1 avatar)\n"
        "• <code>.me</code> — send your card\n"
        "• <code>.ban</code> — block & delete (in a group — leave/ban)\n"
        "• <code>.check</code> — check (limited by tariff)\n\n"
        "<b>The manager bot</b> forwards you: deleted/edited messages, "
        "autoresponder hits, .info/.check results, one-time photos/voice.",
    ),
    "support_text": (
        "🆘 <b>Підтримка</b>\n\nЗ питань — {contact}",
        "🆘 <b>Поддержка</b>\n\nПо вопросам — {contact}",
        "🆘 <b>Support</b>\n\nContact — {contact}",
    ),
    "start_manager_hint": (
        "\n\n❗️ Щоб отримувати сповіщення, запустіть менеджер-бота: {manager}",
        "\n\n❗️ Чтобы получать уведомления, запустите менеджер-бота: {manager}",
        "\n\n❗️ To receive notifications, start the manager bot: {manager}",
    ),
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
    "code_sent": (
        "💬 Код надіслано в Telegram.\nВведіть його кнопками:",
        "💬 Код отправлен в Telegram.\nВведите его кнопками:",
        "💬 A code was sent in Telegram.\nEnter it with the buttons:",
    ),
    "code_label": ("💬 Код: {masked}", "💬 Код: {masked}", "💬 Code: {masked}"),
    "code_too_short": ("Код закороткий!", "Код слишком короткий!", "Code is too short!"),
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
        "✅ Акаунт підключено!\n\n👤 {name}\n🆔 <code>{user_id}</code>\n📱 {phone}",
        "✅ Аккаунт подключён!\n\n👤 {name}\n🆔 <code>{user_id}</code>\n📱 {phone}",
        "✅ Account connected!\n\n👤 {name}\n🆔 <code>{user_id}</code>\n📱 {phone}",
    ),
    # subscribe
    "sub_choose_method": ("Оберіть спосіб оплати:", "Выберите способ оплаты:", "Choose a payment method:"),
    "sub_buy": ("💳 Купити", "💳 Купить", "💳 Buy"),
    "sub_in_dev_btn": ("🚧 в розробці", "🚧 в разработке", "🚧 in development"),
    "sub_back": ("◀️ Назад", "◀️ Назад", "◀️ Back"),
    "sub_pay_stars": ("⭐ Telegram Stars — {stars}⭐", "⭐ Telegram Stars — {stars}⭐", "⭐ Telegram Stars — {stars}⭐"),
    "sub_pay_crypto": ("💎 Crypto — {amount} USDT", "💎 Crypto — {amount} USDT", "💎 Crypto — {amount} USDT"),
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
    "sub_connect_hint": (
        "Підключіть акаунт: ⚙️ Налаштування → 🔗 Прив'язати акаунт.",
        "Подключите аккаунт: ⚙️ Настройки → 🔗 Привязать аккаунт.",
        "Connect your account: ⚙️ Settings → 🔗 Link account.",
    ),
    "sub_stars_title": ("Підписка {title}", "Подписка {title}", "{title} subscription"),
    "sub_stars_desc": ("Тариф {title} на 30 днів", "Тариф {title} на 30 дней", "{title} tariff for 30 days"),
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
        "⚠️ Ваш акаунт відключено — сесія недійсна. Прив'яжіть акаунт заново в боті.",
        "⚠️ Ваш аккаунт отключён — сессия недействительна. Привяжите аккаунт заново в боте.",
        "⚠️ Your account was disconnected — the session is invalid. Re-link it in the bot.",
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
        ["Усе зі Standard", ".check — 10/місяць", "Автовідповідач (налаштовується)"],
        ["Всё из Standard", ".check — 10/месяц", "Автоответчик (настраивается)"],
        ["Everything in Standard", ".check — 10/month", "Autoresponder (configurable)"],
    ),
    "premium": (
        ["🚧 В розробці"],
        ["🚧 В разработке"],
        ["🚧 In development"],
    ),
}
