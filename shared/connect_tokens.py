"""One-time connect tokens for the Mini App login flow.

Session-injected, like `db/queries.py` and `shared/referrals.py` — functions
take an `AsyncSession` and flush (but don't commit); the caller owns the
transaction. All time inputs are passed in explicitly so tests are
deterministic; stored naive UTC (this codebase's convention — see
`management_bot/subscriptions.py::_naive_utc`).

The server holds nothing else about an in-progress login. The old
LoginManager kept a live Telethon client per user in process memory and lost
it on every restart; here the only server-side state is this row and its
TTL — the actual login lives in the browser's localStorage until the session
is handed off and the token is consumed.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ConnectToken

DEFAULT_TTL_MINUTES = 15
DEFAULT_RATE_LIMIT_SECONDS = 60


def _naive_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


async def check_rate_limit(
    session: AsyncSession, telegram_id: int, *, window_seconds: int = DEFAULT_RATE_LIMIT_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Whether this user is allowed to be issued a new token right now."""
    now = _naive_utc(now)
    latest = (
        await session.execute(
            select(ConnectToken.created_at)
            .where(ConnectToken.telegram_id == telegram_id)
            .order_by(ConnectToken.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return True
    return (now - latest).total_seconds() >= window_seconds


async def get_active_token(
    session: AsyncSession, telegram_id: int, *, now: datetime | None = None
) -> ConnectToken | None:
    """This user's in-progress attempt, if any: issued, not yet used, not yet
    expired. Starting a second attempt while one is live would leave the first
    one's Mini App button in the chat still pointing at a token that the user
    is no longer looking at — so the entry point checks this first and tells
    them to finish (or wait out) the one they already have."""
    now = _naive_utc(now)
    return (
        await session.execute(
            select(ConnectToken)
            .where(
                ConnectToken.telegram_id == telegram_id,
                ConnectToken.used_at.is_(None),
                ConnectToken.expires_at > now,
            )
            .order_by(ConnectToken.expires_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def claim_expired_for_notice(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 50
) -> list[ConnectToken]:
    """Atomically claim expired-but-unused tokens that still owe their owner a
    "your link ran out" message, marking them notified in the same statement.

    Same WHERE-guarded UPDATE ... RETURNING as `consume_token`, and for the
    same reason: two sweeper ticks (or two processes) must never both claim
    the same row and send the user a duplicate message.
    """
    now = _naive_utc(now)
    due = (
        select(ConnectToken.id)
        .where(
            ConnectToken.used_at.is_(None),
            ConnectToken.expiry_notified_at.is_(None),
            ConnectToken.expires_at <= now,
        )
        .order_by(ConnectToken.expires_at)
        .limit(limit)
        .scalar_subquery()
    )
    result = await session.execute(
        update(ConnectToken)
        .where(ConnectToken.id.in_(due))
        .values(expiry_notified_at=now)
        .returning(ConnectToken)
    )
    rows = list(result.scalars().all())
    await session.flush()
    return rows


async def create_token(
    session: AsyncSession,
    *,
    telegram_id: int,
    chat_id: int,
    phone: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    now: datetime | None = None,
) -> ConnectToken:
    now = _naive_utc(now)
    if not await check_rate_limit(session, telegram_id, now=now):
        raise ValueError("rate limit exceeded — try again in a minute")
    row = ConnectToken(
        token=secrets.token_urlsafe(32),
        telegram_id=telegram_id,
        chat_id=chat_id,
        phone=phone,
        expires_at=now + timedelta(minutes=ttl_minutes),
        # Explicit, not the column's server_default=func.now() — the rate
        # limit check reads this back and compares it to a caller-supplied
        # `now`, so it must be the SAME clock, not the DB's real wall time.
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def set_message_id(session: AsyncSession, token_id: int, message_id: int) -> None:
    """Filled in after the bot message with the Mini App button is sent — the
    id isn't known until Telegram returns it — so the button can be removed
    once login succeeds."""
    row = await session.get(ConnectToken, token_id)
    if row is not None:
        row.message_id = message_id
        await session.flush()


async def get_valid_token(session: AsyncSession, token: str, *, now: datetime | None = None) -> ConnectToken | None:
    """`None` if the token doesn't exist, is expired, or was already consumed."""
    now = _naive_utc(now)
    return (
        await session.execute(
            select(ConnectToken).where(
                ConnectToken.token == token,
                ConnectToken.used_at.is_(None),
                ConnectToken.expires_at > now,
            )
        )
    ).scalar_one_or_none()


async def consume_token(session: AsyncSession, token: str, *, now: datetime | None = None) -> ConnectToken | None:
    """Atomically mark a token used, returning it only if this call is the one
    that consumed it. Guards the race between two concurrent /session posts
    (e.g. a double-tap, or the resumed page and the original tab both firing)
    — without the WHERE-guarded UPDATE, both could "succeed" and save two
    sessions for what looks like one login."""
    now = _naive_utc(now)
    result = await session.execute(
        update(ConnectToken)
        .where(
            ConnectToken.token == token,
            ConnectToken.used_at.is_(None),
            ConnectToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(ConnectToken)
    )
    row = result.scalar_one_or_none()
    await session.flush()
    return row
