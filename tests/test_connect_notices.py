"""The "your connect link ran out" chat notice.

Verifiable only here in practice: live it takes a 15-minute wait to reach,
and the whole point of the sweep is that it still fires after a restart.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from management_bot import connect_notices
from management_bot.storage import upsert_user
from shared.connect_tokens import consume_token, create_token, set_message_id

NOW = datetime(2026, 9, 1, 12, 0, 0)
LATER = NOW + timedelta(minutes=16)


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.deleted: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

    async def delete_message(self, chat_id, message_id):
        self.deleted.append({"chat_id": chat_id, "message_id": message_id})


def _patch_session(monkeypatch, db_session):
    @asynccontextmanager
    async def fake_get_session():
        yield db_session

    monkeypatch.setattr(connect_notices, "get_session", fake_get_session)


async def _expired_token(db_session, *, telegram_id=4242, message_id=777):
    await upsert_user(db_session, telegram_id, username="tester", language_code="uk")
    token = await create_token(
        db_session, telegram_id=telegram_id, chat_id=telegram_id, phone="+380671234567", now=NOW
    )
    await set_message_id(db_session, token.id, message_id)
    await db_session.commit()
    return token


async def test_expired_attempt_gets_a_notice_with_a_settings_button(db_session, monkeypatch):
    await _expired_token(db_session)
    _patch_session(monkeypatch, db_session)
    bot = _FakeBot()

    sent_count = await connect_notices._tick(bot, now=LATER)

    assert sent_count == 1
    assert len(bot.sent) == 1
    # The dead Mini App button must go with it.
    assert bot.deleted == [{"chat_id": 4242, "message_id": 777}]

    kb = bot.sent[0]["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "set:back"


async def test_a_finished_login_is_never_told_it_expired(db_session, monkeypatch):
    token = await _expired_token(db_session, telegram_id=4343)
    await consume_token(db_session, token.token, now=NOW + timedelta(minutes=3))
    await db_session.commit()

    _patch_session(monkeypatch, db_session)
    bot = _FakeBot()

    assert await connect_notices._tick(bot, now=LATER) == 0
    assert bot.sent == []


async def test_the_notice_is_sent_only_once(db_session, monkeypatch):
    await _expired_token(db_session, telegram_id=4444)
    _patch_session(monkeypatch, db_session)
    bot = _FakeBot()

    await connect_notices._tick(bot, now=LATER)
    await connect_notices._tick(bot, now=LATER)

    assert len(bot.sent) == 1


async def test_one_failed_send_does_not_block_the_rest(db_session, monkeypatch):
    """A courtesy message failing for one user must not strand the others."""
    await _expired_token(db_session, telegram_id=5001, message_id=1)
    await _expired_token(db_session, telegram_id=5002, message_id=2)
    _patch_session(monkeypatch, db_session)

    bot = _FakeBot()
    original = bot.send_message

    async def flaky(chat_id, text, **kwargs):
        if chat_id == 5001:
            raise RuntimeError("blocked by user")
        await original(chat_id, text, **kwargs)

    bot.send_message = flaky

    await connect_notices._tick(bot, now=LATER)
    assert [m["chat_id"] for m in bot.sent] == [5002]
