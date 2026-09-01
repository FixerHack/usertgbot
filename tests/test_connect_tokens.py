"""One-time connect tokens — TTL, rate limiting, atomic consumption.

The server holds nothing else about an in-progress Mini App login, so this
module IS the entire lifecycle: issue, validate, and consume exactly once.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from shared.connect_tokens import (
    claim_expired_for_notice,
    consume_token,
    create_token,
    get_active_token,
    get_valid_token,
)

NOW = datetime(2026, 9, 1, 12, 0, 0)


async def test_create_and_get_valid_token(db_session):
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+380671234567", now=NOW)
    assert token.token
    assert token.expires_at == NOW + timedelta(minutes=15)
    assert token.used_at is None

    got = await get_valid_token(db_session, token.token, now=NOW)
    assert got is not None and got.id == token.id


async def test_token_expires(db_session):
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    still_valid = await get_valid_token(db_session, token.token, now=NOW + timedelta(minutes=14, seconds=59))
    assert still_valid is not None
    expired = await get_valid_token(db_session, token.token, now=NOW + timedelta(minutes=15, seconds=1))
    assert expired is None


async def test_unknown_token_is_invalid(db_session):
    assert await get_valid_token(db_session, "does-not-exist", now=NOW) is None


async def test_custom_ttl(db_session):
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", ttl_minutes=5, now=NOW)
    assert token.expires_at == NOW + timedelta(minutes=5)


async def test_rate_limit_blocks_rapid_reissue(db_session):
    await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    try:
        await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW + timedelta(seconds=30))
        assert False, "should have raised"
    except ValueError as exc:
        assert "rate limit" in str(exc)


async def test_rate_limit_resets_after_window(db_session):
    await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    second = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW + timedelta(seconds=61))
    assert second is not None


async def test_rate_limit_is_per_user(db_session):
    await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    # a different user is unaffected by user 1's rate limit
    other = await create_token(db_session, telegram_id=2, chat_id=2, phone="+2", now=NOW)
    assert other is not None


async def test_consume_marks_used_and_returns_the_row(db_session):
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    consumed = await consume_token(db_session, token.token, now=NOW + timedelta(minutes=1))
    assert consumed is not None
    assert consumed.used_at == NOW + timedelta(minutes=1)


async def test_consumed_token_is_no_longer_valid(db_session):
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    await consume_token(db_session, token.token, now=NOW)
    assert await get_valid_token(db_session, token.token, now=NOW) is None


async def test_consuming_twice_only_succeeds_once(db_session):
    """The core guarantee: a second POST /session with the same token (double
    tap, or the original tab firing after the resumed one already succeeded)
    must not silently re-save a second session."""
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    first = await consume_token(db_session, token.token, now=NOW)
    second = await consume_token(db_session, token.token, now=NOW)
    assert first is not None
    assert second is None


async def test_consuming_an_expired_token_fails(db_session):
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    result = await consume_token(db_session, token.token, now=NOW + timedelta(minutes=16))
    assert result is None


async def test_consuming_unknown_token_returns_none(db_session):
    assert await consume_token(db_session, "does-not-exist", now=NOW) is None


async def test_concurrent_consume_only_one_wins(db_session):
    """Two "simultaneous" POST /session calls for the same token — only one
    may succeed, matching the DB-level WHERE-guarded UPDATE."""
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    results = await asyncio.gather(
        consume_token(db_session, token.token, now=NOW),
        consume_token(db_session, token.token, now=NOW),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


# --- in-progress attempt guard ------------------------------------------------


async def test_get_active_token_finds_a_live_attempt(db_session):
    token = await create_token(db_session, telegram_id=7, chat_id=7, phone="+1", now=NOW)
    found = await get_active_token(db_session, 7, now=NOW + timedelta(minutes=5))
    assert found is not None and found.id == token.id


async def test_get_active_token_ignores_expired_and_used(db_session):
    expired = await create_token(db_session, telegram_id=7, chat_id=7, phone="+1", now=NOW)
    assert await get_active_token(db_session, 7, now=NOW + timedelta(minutes=16)) is None

    await consume_token(db_session, expired.token, now=NOW)
    assert await get_active_token(db_session, 7, now=NOW) is None


async def test_get_active_token_is_per_user(db_session):
    await create_token(db_session, telegram_id=7, chat_id=7, phone="+1", now=NOW)
    assert await get_active_token(db_session, 8, now=NOW) is None


# --- expiry notification sweep ------------------------------------------------


async def test_claim_expired_returns_only_expired_unused(db_session):
    stale = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    fresh = await create_token(
        db_session, telegram_id=2, chat_id=2, phone="+2", now=NOW + timedelta(minutes=10)
    )

    claimed = await claim_expired_for_notice(db_session, now=NOW + timedelta(minutes=16))
    ids = {row.id for row in claimed}
    assert stale.id in ids
    assert fresh.id not in ids  # still has 9 minutes left


async def test_claim_expired_skips_a_completed_login(db_session):
    """A user who finished in time must never be told their link ran out."""
    token = await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    await consume_token(db_session, token.token, now=NOW + timedelta(minutes=2))

    claimed = await claim_expired_for_notice(db_session, now=NOW + timedelta(minutes=16))
    assert claimed == []


async def test_claim_expired_never_notifies_twice(db_session):
    await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    later = NOW + timedelta(minutes=16)

    first = await claim_expired_for_notice(db_session, now=later)
    second = await claim_expired_for_notice(db_session, now=later)
    assert len(first) == 1
    assert second == []


async def test_concurrent_sweeps_claim_each_token_once(db_session):
    """Two sweeper ticks racing must not double-message the user."""
    await create_token(db_session, telegram_id=1, chat_id=1, phone="+1", now=NOW)
    later = NOW + timedelta(minutes=16)

    batches = await asyncio.gather(
        claim_expired_for_notice(db_session, now=later),
        claim_expired_for_notice(db_session, now=later),
    )
    assert sum(len(b) for b in batches) == 1
