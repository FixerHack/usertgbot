"""Re-linking an account must never strand the login the user just completed.

A real production incident, seen twice within ten minutes of going live:
save_session upserts onto the SAME row, so a worker still holding the OLD
(revoked) key would mark that row inactive when its key finally failed — and
by then the row already held the user's brand new, working session. The owner
saw "not connected" seconds after a successful re-link.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from db import queries
from db.models import Session
from management_bot.storage import save_session, upsert_user
from userbot import worker


async def _linked(db_session, telegram_id=9001, session_string="first"):
    await upsert_user(db_session, telegram_id, username="owner")
    row = await save_session(
        db_session, telegram_id=telegram_id, phone_number="+15550001111", session_string=session_string
    )
    await db_session.commit()
    return row


async def test_guarded_deactivate_skips_a_row_that_was_re_linked(db_session):
    row = await _linked(db_session)
    stale_blob = row.encrypted_session

    # the user re-links: same row, new session string, is_active back to True
    await save_session(
        db_session, telegram_id=9001, phone_number="+15550001111", session_string="second"
    )
    await db_session.commit()

    did = await queries.deactivate_session(db_session, row.id, only_if_session_is=stale_blob)
    await db_session.commit()

    assert did is False, "a worker holding the OLD key must not touch the NEW session"
    assert (await db_session.get(Session, row.id)).is_active is True


async def test_guarded_deactivate_still_works_when_nothing_changed(db_session):
    row = await _linked(db_session, telegram_id=9002)

    did = await queries.deactivate_session(db_session, row.id, only_if_session_is=row.encrypted_session)
    await db_session.commit()

    assert did is True
    assert (await db_session.get(Session, row.id)).is_active is False


async def test_unguarded_deactivate_is_unchanged(db_session):
    """Callers that legitimately want an unconditional unlink (the /settings
    "unlink account" button) must keep working."""
    row = await _linked(db_session, telegram_id=9003)
    assert await queries.deactivate_session(db_session, row.id) is True
    await db_session.commit()
    assert (await db_session.get(Session, row.id)).is_active is False


async def test_worker_does_not_notify_when_the_row_was_re_linked(db_session, monkeypatch):
    """The owner must not be told their account died when it demonstrably
    hasn't — they just reconnected it."""
    row = await _linked(db_session, telegram_id=9004)
    stale_blob = row.encrypted_session
    await save_session(
        db_session, telegram_id=9004, phone_number="+15550001111", session_string="second"
    )
    await db_session.commit()

    @asynccontextmanager
    async def fake_get_session():
        yield db_session

    monkeypatch.setattr(worker, "get_session", fake_get_session)

    sent: list[str] = []

    class _FakeNotifier:
        def __init__(self, token):
            pass

        async def send_text(self, chat_id, text, **kw):
            sent.append(text)
            return True

        async def close(self):
            pass

    monkeypatch.setattr(worker, "ManagerNotifier", _FakeNotifier)

    ctx = SimpleNamespace(owner_telegram_id=9004)
    await worker._mark_session_dead(ctx, row.id, "uk", "AuthKeyUnregisteredError", stale_blob)

    assert sent == [], "no 'your account died' message for a session that was just re-linked"
    assert (await db_session.get(Session, row.id)).is_active is True
