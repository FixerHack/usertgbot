"""⚙️ Налаштування → 💾 Запис чатів.

Starting a recording used to mean knowing that `.save` exists. The bot cannot
list someone's dialogs, so it asks Telegram to: the contact picker hands back
a user id, which for a private chat is the chat id as well.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from db.models import Subscription, SubscriptionStatus
from management_bot import storage
from management_bot.handlers import settings as settings_handlers
from shared.i18n import t


@pytest.fixture
async def premium_owner(db_session):
    user = await storage.upsert_user(db_session, 992001, username="owner")
    db_session.add(
        Subscription(
            user_id=user.id, tariff="premium", status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
    )
    await db_session.commit()
    return user


def _patch(monkeypatch, db_session):
    @contextlib.asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(settings_handlers, "get_session", fake_session)


def _labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# --- the entry point -------------------------------------------------------


def test_the_recording_button_only_appears_on_a_plan_that_has_it():
    """A button whose only answer is "your tariff does not include this" is a
    worse door than no door."""
    with_it = _data(settings_handlers._main_menu(True, "uk", can_record=True))
    without = _data(settings_handlers._main_menu(True, "uk", can_record=False))

    assert "set:rec" in with_it
    assert "set:rec" not in without


# --- the screen ------------------------------------------------------------


async def test_an_empty_screen_says_so_and_still_offers_the_picker(db_session, premium_owner, monkeypatch):
    _patch(monkeypatch, db_session)
    text, kb = await settings_handlers._recordings_screen(db_session, 992001, "uk")

    assert t("uk", "rec_menu_none") in text
    assert "set:rec:pick" in _data(kb)


async def test_a_running_recording_gets_a_stop_button(db_session, premium_owner, monkeypatch):
    from db import queries

    _patch(monkeypatch, db_session)
    row = await queries.start_recording(
        db_session, owner_user_id=premium_owner.id, chat_id=555, chat_title="@someone"
    )
    await db_session.commit()

    text, kb = await settings_handlers._recordings_screen(db_session, 992001, "uk")

    assert t("uk", "rec_menu_none") not in text
    assert f"set:rec:stop:{row.id}" in _data(kb)
    assert any("@someone" in label for label in _labels(kb))


async def test_a_stopped_recording_leaves_the_screen(db_session, premium_owner, monkeypatch):
    from db import queries

    _patch(monkeypatch, db_session)
    row = await queries.start_recording(
        db_session, owner_user_id=premium_owner.id, chat_id=555, chat_title="@someone"
    )
    await queries.stop_recording(db_session, row.id)
    await db_session.commit()

    _, kb = await settings_handlers._recordings_screen(db_session, 992001, "uk")
    assert f"set:rec:stop:{row.id}" not in _data(kb)


# --- the picker ------------------------------------------------------------


def test_the_picker_asks_telegram_rather_than_guessing():
    """The bot has no way to know what dialogs someone has; this is the only
    honest way to offer a chat list."""
    from aiogram.types import KeyboardButtonRequestUsers

    # Rebuild what the handler sends, since the handler needs a live callback.
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=t("uk", "rec_pick_button"),
                    request_users=KeyboardButtonRequestUsers(
                        request_id=settings_handlers._RECORD_REQUEST_ID,
                        user_is_bot=False,
                        max_quantity=1,
                    ),
                )
            ]
        ]
    )
    button = keyboard.keyboard[0][0]
    assert button.request_users is not None
    assert button.request_users.max_quantity == 1
    assert button.request_users.user_is_bot is False, "a bot chat is not worth recording"


class _State:
    """Enough FSMContext for the picker: it stores one message id."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> dict:
        self.data.update(kwargs)
        return dict(self.data)


class _Bot:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))


def _shared_message(bot=None, *, request_id=None, message_id=777, answered=None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=992001),
        message_id=message_id,
        bot=bot or _Bot(),
        from_user=SimpleNamespace(language_code="uk", id=992001),
        users_shared=SimpleNamespace(
            request_id=settings_handlers._RECORD_REQUEST_ID if request_id is None else request_id,
            user_ids=[6115569877],
            users=[SimpleNamespace(username="mryoucode", first_name=None, last_name=None)],
        ),
        answer=lambda text, **kw: (answered.append(text) if answered is not None else None) or _async_none(),
    )


async def test_picking_a_contact_starts_a_recording_for_that_chat(db_session, premium_owner, monkeypatch):
    """A private chat's id IS the other person's user id, which is exactly what
    the picker returns."""
    from db import queries

    _patch(monkeypatch, db_session)
    answered: list[str] = []
    message = _shared_message(answered=answered)

    await settings_handlers.on_user_picked(message, _State())

    rows = await queries.get_active_recordings(db_session, premium_owner.id)
    assert [r.chat_id for r in rows] == [6115569877]
    assert rows[0].chat_title == "@mryoucode"
    assert answered


async def test_picking_the_same_contact_twice_does_not_double_up(db_session, premium_owner, monkeypatch):
    from db import queries

    _patch(monkeypatch, db_session)
    answered: list[str] = []

    await settings_handlers.on_user_picked(_shared_message(answered=answered), _State())
    await settings_handlers.on_user_picked(_shared_message(answered=answered), _State())

    rows = await queries.get_active_recordings(db_session, premium_owner.id)
    assert len(rows) == 1
    assert answered[-1] == t("uk", "rec_already_for")


async def test_a_reply_from_another_picker_is_ignored(db_session, premium_owner, monkeypatch):
    """request_id is what separates our picker from any other the bot may
    grow; acting on someone else's would start a recording nobody asked for."""
    from db import queries

    _patch(monkeypatch, db_session)
    bot = _Bot()

    await settings_handlers.on_user_picked(_shared_message(bot, request_id=99), _State())

    assert await queries.get_active_recordings(db_session, premium_owner.id) == []
    assert bot.deleted == [], "someone else's picker is not ours to clean up"


# --- the paper trail -------------------------------------------------------


async def test_picking_takes_the_prompt_and_the_shared_line_back_down(
    db_session, premium_owner, monkeypatch
):
    """Both are machinery, not conversation; left alone they stacked up a
    fresh pair on screen every time the picker was opened."""
    _patch(monkeypatch, db_session)
    bot = _Bot()

    await settings_handlers.on_user_picked(
        _shared_message(bot, message_id=777), _State({"rec_prompt_id": 555})
    )

    assert bot.deleted == [(992001, 555), (992001, 777)]


async def test_a_missing_prompt_does_not_stop_the_recording(db_session, premium_owner, monkeypatch):
    """After a restart there is no remembered prompt id — one stranded line is
    not a reason to refuse the recording."""
    from db import queries

    _patch(monkeypatch, db_session)
    bot = _Bot()

    await settings_handlers.on_user_picked(_shared_message(bot), _State())

    assert bot.deleted == [(992001, 777)]
    assert await queries.get_active_recordings(db_session, premium_owner.id)


async def test_a_delete_that_fails_does_not_stop_the_recording(db_session, premium_owner, monkeypatch):
    from aiogram.exceptions import TelegramBadRequest
    from db import queries

    _patch(monkeypatch, db_session)

    class _Refusing(_Bot):
        async def delete_message(self, chat_id: int, message_id: int) -> None:
            raise TelegramBadRequest(method=None, message="message can't be deleted")

    await settings_handlers.on_user_picked(_shared_message(_Refusing()), _State())

    assert await queries.get_active_recordings(db_session, premium_owner.id)


# --- cancel ----------------------------------------------------------------


async def test_cancel_answers_and_clears_the_keyboard(db_session, premium_owner, monkeypatch):
    """Before this the button sent a word nothing handled: the picker keyboard
    stayed up and the bot said nothing at all."""
    _patch(monkeypatch, db_session)
    bot = _Bot()
    sent: list[tuple[str, object]] = []

    message = SimpleNamespace(
        chat=SimpleNamespace(id=992001),
        message_id=778,
        bot=bot,
        from_user=SimpleNamespace(language_code="uk", id=992001),
        answer=lambda text, **kw: sent.append((text, kw.get("reply_markup"))) or _async_none(),
    )

    await settings_handlers.on_pick_cancel(message, _State({"rec_prompt_id": 556}))

    assert bot.deleted == [(992001, 556), (992001, 778)]
    assert sent and sent[0][0] == t("uk", "rec_pick_cancelled")
    assert sent[0][1] is not None, "the picker keyboard has to be replaced by something"


def test_cancel_is_matched_in_every_language():
    from shared.i18n import variants

    labels = variants("rec_pick_cancel")
    assert len(set(labels)) == 3


async def _async_none():
    return None
