"""Persistence helpers for management_bot.

Functions take an `AsyncSession` and flush (but don't commit) — the caller owns
the transaction boundary. That keeps them trivially testable against an
in-memory SQLite session while production wraps them in `db.session.get_session`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Session, Subscription, SubscriptionStatus, User
from db.queries import as_aware
from management_bot.crypto import encrypt_session


def normalize_phone(raw: str) -> str:
    """`"+380 (67) 123-45-67"` -> `"+380671234567"`."""
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if digits else ""


@dataclass
class SubscriptionInfo:
    tariff: str
    status: str
    expires_at: datetime | None


def _is_expired(sub: Subscription, now: datetime) -> bool:
    return sub.expires_at is not None and as_aware(sub.expires_at) <= now


def _effective_status(sub: Subscription, now: datetime) -> str:
    """What the subscription IS, not what its row still says.

    Nothing sweeps the table to flip ACTIVE to EXPIRED when a period ends, so
    the stored status outlives the period it describes.
    """
    raw = sub.status.value if hasattr(sub.status, "value") else str(sub.status)
    if raw == SubscriptionStatus.ACTIVE.value and _is_expired(sub, now):
        return SubscriptionStatus.EXPIRED.value
    return raw


@dataclass
class SessionInfo:
    phone_number: str
    last_used_at: datetime | None


@dataclass
class UserStatus:
    known: bool
    subscription: SubscriptionInfo | None = None
    sessions: list[SessionInfo] = field(default_factory=list)


async def upsert_user(
    session: AsyncSession,
    telegram_id: int,
    *,
    username: str | None = None,
    full_name: str | None = None,
    language_code: str | None = None,
) -> User:
    """Insert the user or update mutable profile fields, returning the row."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            language_code=language_code,
        )
        session.add(user)
        await session.flush()
        return user
    if username is not None:
        user.username = username
    if full_name is not None:
        user.full_name = full_name
    if language_code is not None and not user.language_locked:
        user.language_code = language_code
    await session.flush()
    return user


async def save_session(
    session: AsyncSession,
    *,
    telegram_id: int,
    phone_number: str,
    session_string: str,
    username: str | None = None,
    full_name: str | None = None,
) -> Session:
    """Encrypt and persist a Telethon session string for the given user.

    Upserts on (user, phone) — reconnecting the same number replaces the old
    session rather than piling up rows (mirrors the DB unique constraint).
    """
    user = await upsert_user(session, telegram_id, username=username, full_name=full_name)
    encrypted = encrypt_session(session_string)

    result = await session.execute(
        select(Session).where(Session.user_id == user.id, Session.phone_number == phone_number)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = Session(
            user_id=user.id,
            phone_number=phone_number,
            encrypted_session=encrypted,
            is_active=True,
        )
        session.add(row)
    else:
        row.encrypted_session = encrypted
        row.is_active = True
    await session.flush()
    return row


async def get_last_known_phone(session: AsyncSession, telegram_id: int) -> str | None:
    """The phone from this user's most recent session, active or not.

    Session rows survive deactivation (unlink, dead-session detection) — see
    save_session's upsert-on-(user, phone) and queries.deactivate_session,
    neither of which deletes the row. Letting /connect check this first means
    a re-link after unlinking doesn't have to ask for the contact button
    again; Bot API has no way to read a phone from the profile directly (even
    with "everyone" visibility), so a previous session is the only source
    that's actually available.
    """
    # Ordered by id, not created_at: SQLite's CURRENT_TIMESTAMP only has
    # second resolution, so two sessions created within the same second would
    # tie under created_at and could return the wrong one — the id is a
    # reliable insertion order regardless of clock granularity.
    result = await session.execute(
        select(Session.phone_number)
        .join(User, User.id == Session.user_id)
        .where(User.telegram_id == telegram_id)
        .order_by(Session.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_user_status(session: AsyncSession, telegram_id: int) -> UserStatus:
    """Subscription + active-session summary for the /status command."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        return UserStatus(known=False)

    subs = (
        (
            await session.execute(
                select(Subscription)
                .where(Subscription.user_id == user.id)
                # id, not created_at: two rows written in the same second tie
                # on the timestamp, and renewals make that likelier.
                .order_by(Subscription.id.desc())
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    # An ACTIVE row whose period has run out is not active. The command gate
    # (db.queries.get_active_subscription_for_user) has always known that;
    # this screen did not, so a lapsed subscriber was told "active" while
    # every command was already being refused.
    chosen = next(
        (s for s in subs if s.status == SubscriptionStatus.ACTIVE and not _is_expired(s, now)),
        None,
    )
    if chosen is None:
        # Nothing live. Prefer the last subscription that was actually paid
        # for over the newest row: tapping "buy" and walking away leaves a
        # PENDING row behind, and showing that to someone whose Pro just
        # lapsed tells them they are mid-purchase instead of that their
        # subscription ended.
        chosen = next((s for s in subs if s.started_at is not None), None)
    if chosen is None and subs:
        chosen = subs[0]
    sub_info = (
        SubscriptionInfo(
            tariff=chosen.tariff,
            status=_effective_status(chosen, now),
            expires_at=chosen.expires_at,
        )
        if chosen is not None
        else None
    )

    sessions = (
        (
            await session.execute(
                select(Session)
                .where(Session.user_id == user.id, Session.is_active.is_(True))
                .order_by(Session.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    session_infos = [SessionInfo(phone_number=s.phone_number, last_used_at=s.last_used_at) for s in sessions]

    return UserStatus(known=True, subscription=sub_info, sessions=session_infos)
