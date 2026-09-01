"""cmd_connect: known-phone fast path.

Regression cover for a real annoyance: re-linking after an unlink or a dead
session made the user tap "share contact" again even though the phone was
already on file from a previous connect. Bot API has no way to read a
profile's phone directly (even set to "everyone" visible), so the DB is the
only source — this is what lets /connect skip straight past the ask.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from db.models import Subscription, SubscriptionStatus
from management_bot import storage
from management_bot.handlers import connect


class _FakeState:
    """Just enough of aiogram's FSMContext for cmd_connect to run."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def set_state(self, state) -> None:
        self.calls.append(f"set_state:{state}")

    async def clear(self) -> None:
        self.calls.append("clear")


class _FakeMessage:
    def __init__(self, telegram_id: int) -> None:
        self.chat = SimpleNamespace(id=telegram_id)
        self.from_user = SimpleNamespace(
            id=telegram_id, full_name="Tester", username="tester", language_code="uk"
        )
        self.answered: list[dict] = []

    async def answer(self, text, **kwargs):
        self.answered.append({"text": text, **kwargs})


async def _with_active_subscription(db_session, telegram_id: int) -> None:
    user = await storage.upsert_user(db_session, telegram_id, username="tester")
    db_session.add(
        Subscription(user_id=user.id, tariff="pro", status=SubscriptionStatus.ACTIVE, payment_provider="stub")
    )
    await db_session.commit()


def _patch_session(monkeypatch, db_session) -> None:
    @asynccontextmanager
    async def fake_get_session():
        yield db_session

    monkeypatch.setattr(connect, "get_session", fake_get_session)


async def test_known_phone_skips_the_contact_request(db_session, monkeypatch):
    telegram_id = 7001
    await _with_active_subscription(db_session, telegram_id)
    await storage.save_session(
        db_session, telegram_id=telegram_id, phone_number="+15551234567", session_string="s"
    )
    await db_session.commit()
    _patch_session(monkeypatch, db_session)

    dispatched: list[tuple] = []

    async def fake_dispatch(message, state, phone):
        dispatched.append((message, state, phone))

    monkeypatch.setattr(connect, "_dispatch_phone", fake_dispatch)

    message = _FakeMessage(telegram_id)
    await connect.cmd_connect(message, state=_FakeState())

    assert len(dispatched) == 1
    assert dispatched[0][2] == "+15551234567"
    assert message.answered == [], "no 'share your contact' prompt when the phone is already known"


async def test_unlinked_session_phone_is_still_reused(db_session, monkeypatch):
    """The whole point: an unlinked (deactivated) session must still count."""
    from db.queries import deactivate_session

    telegram_id = 7002
    await _with_active_subscription(db_session, telegram_id)
    row = await storage.save_session(
        db_session, telegram_id=telegram_id, phone_number="+15551234567", session_string="s"
    )
    await db_session.commit()
    await deactivate_session(db_session, row.id)
    await db_session.commit()
    _patch_session(monkeypatch, db_session)

    dispatched: list[tuple] = []

    async def fake_dispatch(message, state, phone):
        dispatched.append((message, state, phone))

    monkeypatch.setattr(connect, "_dispatch_phone", fake_dispatch)

    message = _FakeMessage(telegram_id)
    await connect.cmd_connect(message, state=_FakeState())

    assert len(dispatched) == 1
    assert dispatched[0][2] == "+15551234567"


async def test_no_known_phone_asks_for_contact(db_session, monkeypatch):
    telegram_id = 7003
    await _with_active_subscription(db_session, telegram_id)
    _patch_session(monkeypatch, db_session)

    dispatched: list[tuple] = []

    async def fake_dispatch(message, state, phone):
        dispatched.append((message, state, phone))

    monkeypatch.setattr(connect, "_dispatch_phone", fake_dispatch)

    message = _FakeMessage(telegram_id)
    await connect.cmd_connect(message, state=_FakeState())

    assert dispatched == []
    assert len(message.answered) == 1
    assert message.answered[0].get("reply_markup") is not None, "must offer the share-contact button"
