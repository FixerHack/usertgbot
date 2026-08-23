"""Cross-service query helpers used by both management_bot and userbot.

Pure DB access over `db.models` only — no imports from any service package.
All time inputs are passed in explicitly so the logic is deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    CheckUsage,
    IgnoredChat,
    MediaBlob,
    SavedMessage,
    Session,
    Subscription,
    SubscriptionStatus,
    User,
    UserSettings,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _month_start(today: date) -> date:
    return today.replace(day=1)


# --- users -----------------------------------------------------------------


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def set_user_language(session: AsyncSession, telegram_id: int, language_code: str | None) -> None:
    """Update a known user's language (no-op if they aren't in the DB yet)."""
    if not language_code:
        return
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is not None:
        user.language_code = language_code
        await session.flush()


# --- sessions --------------------------------------------------------------


async def deactivate_user_sessions(session: AsyncSession, user_id: int) -> int:
    """Mark all of a user's active sessions inactive (unlink). Returns count."""
    result = await session.execute(
        select(Session).where(Session.user_id == user_id, Session.is_active.is_(True))
    )
    rows = result.scalars().all()
    for row in rows:
        row.is_active = False
    await session.flush()
    return len(rows)


async def deactivate_session(session: AsyncSession, session_id: int) -> None:
    result = await session.execute(select(Session).where(Session.id == session_id))
    row = result.scalar_one_or_none()
    if row is not None:
        row.is_active = False
        await session.flush()


# --- subscriptions ---------------------------------------------------------


async def get_active_subscription_for_user(
    session: AsyncSession, user_id: int, *, now: datetime | None = None
) -> Subscription | None:
    """Active, non-expired subscription for an internal user id (latest first)."""
    now = now or _utcnow()
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE)
        .order_by(Subscription.created_at.desc())
    )
    for sub in result.scalars():
        if sub.expires_at is None or _as_aware(sub.expires_at) > now:
            return sub
    return None


async def get_active_subscription_by_telegram_id(
    session: AsyncSession, telegram_id: int, *, now: datetime | None = None
) -> Subscription | None:
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return None
    return await get_active_subscription_for_user(session, user.id, now=now)


def _as_aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as UTC for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# --- .check quota ----------------------------------------------------------


@dataclass
class QuotaResult:
    allowed: bool
    used: int
    quota: int

    @property
    def remaining(self) -> int:
        return max(self.quota - self.used, 0)


async def _get_or_reset_usage(session: AsyncSession, user_id: int, today: date) -> CheckUsage:
    period = _month_start(today)
    result = await session.execute(select(CheckUsage).where(CheckUsage.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = CheckUsage(user_id=user_id, period_start=period, used=0)
        session.add(row)
        await session.flush()
    elif row.period_start < period:
        row.period_start = period
        row.used = 0
        await session.flush()
    return row


async def peek_check_quota(
    session: AsyncSession, user_id: int, quota: int, *, today: date | None = None
) -> QuotaResult:
    row = await _get_or_reset_usage(session, user_id, today or _utcnow().date())
    return QuotaResult(allowed=row.used < quota, used=row.used, quota=quota)


async def consume_check(
    session: AsyncSession, user_id: int, quota: int, *, today: date | None = None
) -> QuotaResult:
    """Try to spend one .check; increments only when allowed."""
    row = await _get_or_reset_usage(session, user_id, today or _utcnow().date())
    if row.used >= quota:
        return QuotaResult(allowed=False, used=row.used, quota=quota)
    row.used += 1
    await session.flush()
    return QuotaResult(allowed=True, used=row.used, quota=quota)


# --- user settings ---------------------------------------------------------


async def get_or_create_settings(session: AsyncSession, user_id: int) -> UserSettings:
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = UserSettings(user_id=user_id)
        session.add(row)
        await session.flush()
    return row


async def set_me_card(session: AsyncSession, user_id: int, card: dict | None) -> UserSettings:
    row = await get_or_create_settings(session, user_id)
    row.me_card = card
    await session.flush()
    return row


async def set_autoresponder(session: AsyncSession, user_id: int, config: dict | None) -> UserSettings:
    row = await get_or_create_settings(session, user_id)
    row.autoresponder = config
    await session.flush()
    return row


# --- saved messages --------------------------------------------------------


async def save_captured_message(
    session: AsyncSession,
    *,
    owner_user_id: int,
    chat_id: int,
    message_id: int,
    event_type: str,
    sender_id: int | None = None,
    text: str | None = None,
    previous_text: str | None = None,
) -> SavedMessage:
    row = SavedMessage(
        owner_user_id=owner_user_id,
        chat_id=chat_id,
        message_id=message_id,
        sender_id=sender_id,
        event_type=event_type,
        text=text,
        previous_text=previous_text,
    )
    session.add(row)
    await session.flush()
    return row


# --- media -----------------------------------------------------------------


async def set_media(
    session: AsyncSession, owner_user_id: int, purpose: str, data: bytes, mime: str | None = None
) -> MediaBlob:
    """Upsert the single image blob for (owner, purpose)."""
    result = await session.execute(
        select(MediaBlob).where(
            MediaBlob.owner_user_id == owner_user_id, MediaBlob.purpose == purpose
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = MediaBlob(owner_user_id=owner_user_id, purpose=purpose, data=data, mime=mime)
        session.add(row)
    else:
        row.data = data
        row.mime = mime
    await session.flush()
    return row


async def delete_media(session: AsyncSession, owner_user_id: int, purpose: str) -> None:
    result = await session.execute(
        select(MediaBlob).where(
            MediaBlob.owner_user_id == owner_user_id, MediaBlob.purpose == purpose
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


async def list_saved_messages(
    session: AsyncSession, owner_user_id: int, *, limit: int = 20
) -> list[SavedMessage]:
    result = await session.execute(
        select(SavedMessage)
        .where(SavedMessage.owner_user_id == owner_user_id)
        .order_by(SavedMessage.id.desc())  # monotonic => chronological, no same-second ties
        .limit(limit)
    )
    return list(result.scalars())


# --- ignored chats -----------------------------------------------------------


async def add_ignored_chat(
    session: AsyncSession, owner_user_id: int, chat_id: int, chat_title: str | None = None
) -> None:
    result = await session.execute(
        select(IgnoredChat).where(
            IgnoredChat.owner_user_id == owner_user_id, IgnoredChat.chat_id == chat_id
        )
    )
    if result.scalar_one_or_none() is not None:
        return
    session.add(IgnoredChat(owner_user_id=owner_user_id, chat_id=chat_id, chat_title=chat_title))
    await session.flush()


async def remove_ignored_chat(session: AsyncSession, owner_user_id: int, chat_id: int) -> None:
    result = await session.execute(
        select(IgnoredChat).where(
            IgnoredChat.owner_user_id == owner_user_id, IgnoredChat.chat_id == chat_id
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


async def list_ignored_chats(session: AsyncSession, owner_user_id: int) -> list[IgnoredChat]:
    result = await session.execute(
        select(IgnoredChat)
        .where(IgnoredChat.owner_user_id == owner_user_id)
        .order_by(IgnoredChat.created_at.desc())
    )
    return list(result.scalars())


async def is_chat_ignored(session: AsyncSession, owner_user_id: int, chat_id: int) -> bool:
    result = await session.execute(
        select(IgnoredChat.chat_id).where(
            IgnoredChat.owner_user_id == owner_user_id, IgnoredChat.chat_id == chat_id
        )
    )
    return result.scalar_one_or_none() is not None
