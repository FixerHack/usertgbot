"""Persistence helpers for management_bot.

Functions take an `AsyncSession` and flush (but don't commit) — the caller owns
the transaction boundary. That keeps them trivially testable against an
in-memory SQLite session while production wraps them in `db.session.get_session`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Session, Subscription, SubscriptionStatus, User
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
                .order_by(Subscription.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    chosen = next((s for s in subs if s.status == SubscriptionStatus.ACTIVE), None)
    if chosen is None and subs:
        chosen = subs[0]
    sub_info = (
        SubscriptionInfo(
            tariff=chosen.tariff,
            status=chosen.status.value if hasattr(chosen.status, "value") else str(chosen.status),
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
