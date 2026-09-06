"""A revoked session must deactivate itself and tell the owner — once.

Regression cover for a real production bug: `connect()` raises
`AuthKeyNotFound` BEFORE `is_user_authorized()` is ever reached, so the
original code (which only checked authorization inside the `async with`)
never deactivated the row and never notified anyone. The supervisor then
restarted the doomed worker every 15s indefinitely while the owner sat there
with a paid subscription and a bot that silently did nothing.
"""

from __future__ import annotations

import pytest
from telethon import errors
from telethon.errors.common import AuthKeyNotFound

from db.models import Session
from management_bot.storage import save_session, upsert_user
from userbot import worker


class _FakeClient:
    """Stands in for TelegramClient: raises on entry, like a dead auth key."""

    def __init__(self, raises: Exception | None = None, authorized: bool = True) -> None:
        self._raises = raises
        self._authorized = authorized
        self.disconnected = False

    async def __aenter__(self):
        if self._raises is not None:
            raise self._raises
        return self

    async def __aexit__(self, *exc):
        return False

    async def is_user_authorized(self) -> bool:
        return self._authorized

    async def run_until_disconnected(self) -> None:
        return None


@pytest.fixture
def captured(monkeypatch):
    """Capture ManagerNotifier.send_text calls and stub out network pieces.

    _mark_session_dead builds its own ManagerNotifier(settings.bot_token) —
    deliberately the MAIN bot's token, not notify_owner's manager-bot one, so
    this notice doesn't depend on an opt-in chat the owner may never have
    started. run_worker's ctx.notifier construction hits the same faked class.
    """
    sent: list[dict] = []

    class _FakeNotifier:
        def __init__(self, token: str) -> None:
            self.token = token

        async def send_text(self, chat_id, text, *, reply_markup=None, **kwargs):
            sent.append({"text": text, "reply_markup": reply_markup})
            return True

        async def close(self) -> None:
            pass

    monkeypatch.setattr(worker, "ManagerNotifier", _FakeNotifier)
    for module in (worker.commands, worker.autosave, worker.autoresponder, worker.viewonce, worker.mute, worker.record, worker.clone):
        monkeypatch.setattr(module, "register", lambda client, ctx: None)
    # The mute cache is primed from the database at startup; these tests are
    # about a dying session, not about what it had muted.
    async def _no_mutes(ctx):
        return None

    monkeypatch.setattr(worker.mute, "load_mutes", _no_mutes)
    monkeypatch.setattr(worker.record, "load_recordings", _no_mutes)
    monkeypatch.setattr(worker.clone, "load_clones", _no_mutes)
    return sent


async def _run(db_session, monkeypatch, client) -> int:
    """Run the worker against `client`, return the session id it was given."""
    await upsert_user(db_session, 5001, username="victim")
    row = await save_session(
        db_session, telegram_id=5001, phone_number="+380000000000", session_string="stub"
    )
    await db_session.commit()

    # StringSession validates its input format, and the client is faked anyway
    monkeypatch.setattr(worker, "StringSession", lambda s: s)
    monkeypatch.setattr(worker, "TelegramClient", lambda **kwargs: client)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_get_session():
        yield db_session

    monkeypatch.setattr(worker, "get_session", fake_get_session)
    await worker.run_worker(row.id, row.user_id, 5001, "uk", "stub")
    return row.id


@pytest.mark.parametrize(
    "error",
    [
        AuthKeyNotFound(),
        errors.AuthKeyUnregisteredError(request=None),
        errors.UserDeactivatedBanError(request=None),
    ],
    ids=["auth_key_not_found", "auth_key_unregistered", "user_banned"],
)
async def test_dead_session_is_deactivated_and_owner_notified(
    db_session, monkeypatch, captured, error
):
    session_id = await _run(db_session, monkeypatch, _FakeClient(raises=error))

    row = await db_session.get(Session, session_id)
    assert row.is_active is False, "a dead session must not be retried forever"
    assert len(captured) == 1, "the owner must be told exactly once"
    assert "сесія" in captured[0]["text"].lower() or "session" in captured[0]["text"].lower()


async def test_notice_carries_a_relink_button(db_session, monkeypatch, captured):
    monkeypatch.setattr(worker.settings, "management_bot_username", "useagentperbot")
    await _run(db_session, monkeypatch, _FakeClient(raises=AuthKeyNotFound()))

    kb = captured[0]["reply_markup"]
    assert kb is not None, "owner should be able to reconnect in one tap"
    url = kb.inline_keyboard[0][0].url
    # must land in the connect flow, not just the bot's dashboard
    assert url == "https://t.me/useagentperbot?start=connect"


async def test_no_button_when_username_not_configured(db_session, monkeypatch, captured):
    monkeypatch.setattr(worker.settings, "management_bot_username", None)
    await _run(db_session, monkeypatch, _FakeClient(raises=AuthKeyNotFound()))

    assert captured[0]["reply_markup"] is None, "notice must still send, just without the button"


async def test_unauthorized_without_raising_also_deactivates(db_session, monkeypatch, captured):
    # the path that already worked: connect() succeeds, authorization is False
    session_id = await _run(db_session, monkeypatch, _FakeClient(authorized=False))

    row = await db_session.get(Session, session_id)
    assert row.is_active is False
    assert len(captured) == 1


async def test_client_identifies_itself_to_the_owner(db_session, monkeypatch, captured):
    """Clients see this in Settings -> Devices. Left to Telethon's defaults it
    reads as an anonymous "PC 64bit" in a foreign datacenter, which invites
    them to terminate the session their subscription depends on."""
    captured_kwargs: dict = {}

    def spy(**kwargs):
        captured_kwargs.update(kwargs)
        return _FakeClient(authorized=True)

    await upsert_user(db_session, 5001, username="victim")
    row = await save_session(
        db_session, telegram_id=5001, phone_number="+380000000000", session_string="stub"
    )
    await db_session.commit()

    monkeypatch.setattr(worker, "StringSession", lambda s: s)
    monkeypatch.setattr(worker, "TelegramClient", spy)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_get_session():
        yield db_session

    monkeypatch.setattr(worker, "get_session", fake_get_session)
    await worker.run_worker(row.id, row.user_id, 5001, "uk", "stub")

    assert captured_kwargs["device_model"] == "User Agent Bot"
    assert captured_kwargs["system_version"]
    assert captured_kwargs["app_version"]


async def test_healthy_session_is_left_alone(db_session, monkeypatch, captured):
    session_id = await _run(db_session, monkeypatch, _FakeClient(authorized=True))

    row = await db_session.get(Session, session_id)
    assert row.is_active is True, "a working session must never be deactivated"
    assert captured == [], "and the owner must not be spammed"
