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


async def test_picking_a_contact_starts_a_recording_for_that_chat(db_session, premium_owner, monkeypatch):
    """A private chat's id IS the other person's user id, which is exactly what
    the picker returns."""
    from db import queries

    _patch(monkeypatch, db_session)
    answered: list[str] = []

    message = SimpleNamespace(
        chat=SimpleNamespace(id=992001),
        from_user=SimpleNamespace(language_code="uk", id=992001),
        users_shared=SimpleNamespace(
            request_id=settings_handlers._RECORD_REQUEST_ID,
            user_ids=[6115569877],
            users=[SimpleNamespace(username="mryoucode", first_name=None, last_name=None)],
        ),
        answer=lambda text, **kw: answered.append(text) or _async_none(),
    )

    await settings_handlers.on_user_picked(message)

    rows = await queries.get_active_recordings(db_session, premium_owner.id)
    assert [r.chat_id for r in rows] == [6115569877]
    assert rows[0].chat_title == "@mryoucode"
    assert answered


async def test_picking_the_same_contact_twice_does_not_double_up(db_session, premium_owner, monkeypatch):
    from db import queries

    _patch(monkeypatch, db_session)
    answered: list[str] = []

    def _message():
        return SimpleNamespace(
            chat=SimpleNamespace(id=992001),
            from_user=SimpleNamespace(language_code="uk", id=992001),
            users_shared=SimpleNamespace(
                request_id=settings_handlers._RECORD_REQUEST_ID,
                user_ids=[6115569877],
                users=[SimpleNamespace(username="mryoucode", first_name=None, last_name=None)],
            ),
            answer=lambda text, **kw: answered.append(text) or _async_none(),
        )

    await settings_handlers.on_user_picked(_message())
    await settings_handlers.on_user_picked(_message())

    rows = await queries.get_active_recordings(db_session, premium_owner.id)
    assert len(rows) == 1
    assert answered[-1] == t("uk", "rec_already_for")


async def test_a_reply_from_another_picker_is_ignored(db_session, premium_owner, monkeypatch):
    """request_id is what separates our picker from any other the bot may
    grow; acting on someone else's would start a recording nobody asked for."""
    from db import queries

    _patch(monkeypatch, db_session)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=992001),
        from_user=SimpleNamespace(language_code="uk", id=992001),
        users_shared=SimpleNamespace(request_id=99, user_ids=[123], users=[]),
        answer=lambda text, **kw: _async_none(),
    )

    await settings_handlers.on_user_picked(message)
    assert await queries.get_active_recordings(db_session, premium_owner.id) == []


async def _async_none():
    return None
