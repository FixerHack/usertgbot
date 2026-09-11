"""Data layer for the admin panel — session-injected so it's testable on SQLite.

Reads/writes go through db.models; the FastAPI routes wrap these with a real
session and commit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import queries
from db.models import CheckUsage, ReferralLink, SavedMessage, Session, Subscription, SubscriptionStatus, User
from shared import referrals
from shared.referrals import ReferralLinkStats
from shared.tariffs import PLANS, Tariff

logger = logging.getLogger(__name__)


def _naive_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(tzinfo=None) if now.tzinfo else now


# --- metrics ---------------------------------------------------------------


@dataclass
class Metrics:
    total_users: int = 0
    blocked_users: int = 0
    connected_accounts: int = 0
    active_subscriptions: int = 0
    by_tariff: dict[str, int] = field(default_factory=dict)
    check_used_total: int = 0
    saved_messages: int = 0
    activity_7d: list[dict] = field(default_factory=list)
    top_users_today: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


async def get_activity_series(
    session: AsyncSession, *, days: int = 7, end: date | None = None
) -> list[dict]:
    """Captured-event (deleted/edited message) counts per day, oldest first."""
    end = end or date.today()
    start_day = end - timedelta(days=days - 1)
    start_dt = datetime.combine(start_day, datetime.min.time())

    rows = await session.execute(
        select(SavedMessage.saved_at).where(SavedMessage.saved_at >= start_dt)
    )
    counts: dict[date, int] = {start_day + timedelta(days=i): 0 for i in range(days)}
    for (saved_at,) in rows:
        d = saved_at.date()
        if d in counts:
            counts[d] += 1
    return [{"date": d.isoformat(), "count": c} for d, c in sorted(counts.items())]


async def get_top_users_today(
    session: AsyncSession, *, day: date | None = None, limit: int = 5
) -> list[dict]:
    """Most active owners today, by captured (deleted/edited) message count."""
    day = day or date.today()
    start_dt = datetime.combine(day, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)

    result = await session.execute(
        select(SavedMessage.owner_user_id, func.count().label("n"))
        .where(SavedMessage.saved_at >= start_dt, SavedMessage.saved_at < end_dt)
        .group_by(SavedMessage.owner_user_id)
        .order_by(func.count().desc())
        .limit(limit)
    )
    rows = result.all()
    if not rows:
        return []

    user_ids = [r[0] for r in rows]
    users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()}
    out = []
    for user_id, count in rows:
        user = users.get(user_id)
        out.append(
            {
                "telegram_id": user.telegram_id if user else None,
                "username": user.username if user else None,
                "full_name": user.full_name if user else None,
                "count": count,
            }
        )
    return out


async def get_metrics(session: AsyncSession, *, now: datetime | None = None) -> Metrics:
    now = _naive_utc(now)

    total_users = await session.scalar(select(func.count()).select_from(User)) or 0
    blocked = await session.scalar(select(func.count()).select_from(User).where(User.is_blocked.is_(True))) or 0
    connected = await session.scalar(
        select(func.count()).select_from(Session).where(Session.is_active.is_(True))
    ) or 0
    check_used = await session.scalar(select(func.coalesce(func.sum(CheckUsage.used), 0))) or 0
    saved = await session.scalar(select(func.count()).select_from(SavedMessage)) or 0

    by_tariff: dict[str, int] = {t.value: 0 for t in Tariff}
    active_total = 0
    result = await session.execute(
        select(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE)
    )
    for sub in result.scalars():
        if sub.expires_at is None or sub.expires_at > now:
            by_tariff[sub.tariff] = by_tariff.get(sub.tariff, 0) + 1
            active_total += 1

    activity_7d = await get_activity_series(session, end=now.date())
    top_users_today = await get_top_users_today(session, day=now.date())

    return Metrics(
        total_users=total_users,
        blocked_users=blocked,
        connected_accounts=connected,
        activity_7d=activity_7d,
        top_users_today=top_users_today,
        active_subscriptions=active_total,
        by_tariff=by_tariff,
        check_used_total=int(check_used),
        saved_messages=saved,
    )


# --- users -----------------------------------------------------------------


@dataclass
class UserRow:
    telegram_id: int
    username: str | None
    full_name: str | None
    joined: str | None
    is_blocked: bool
    tariff: str | None
    sub_status: str | None
    expires_at: str | None
    connected: bool
    referral_code: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


async def list_users(
    session: AsyncSession,
    *,
    tariff: str | None = None,
    blocked: bool | None = None,
    connected: bool | None = None,
    joined_after: date | None = None,
    joined_before: date | None = None,
    limit: int = 200,
    now: datetime | None = None,
) -> list[UserRow]:
    now = _naive_utc(now)
    users = (await session.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    link_codes = dict((await session.execute(select(ReferralLink.id, ReferralLink.code))).all())

    rows: list[UserRow] = []
    for user in users:
        active = await _active_sub(session, user.id, now)
        has_account = bool(
            await session.scalar(
                select(func.count())
                .select_from(Session)
                .where(Session.user_id == user.id, Session.is_active.is_(True))
            )
        )

        if tariff and (active is None or active.tariff != tariff):
            continue
        if blocked is not None and user.is_blocked != blocked:
            continue
        if connected is not None and has_account != connected:
            continue
        if joined_after and (user.created_at is None or user.created_at.date() < joined_after):
            continue
        if joined_before and (user.created_at is None or user.created_at.date() > joined_before):
            continue

        rows.append(
            UserRow(
                telegram_id=user.telegram_id,
                username=user.username,
                full_name=user.full_name,
                joined=user.created_at.date().isoformat() if user.created_at else None,
                is_blocked=user.is_blocked,
                tariff=active.tariff if active else None,
                sub_status=(active.status.value if active else None),
                expires_at=active.expires_at.date().isoformat() if active and active.expires_at else None,
                connected=has_account,
                referral_code=link_codes.get(user.referred_by_link_id),
            )
        )
        if len(rows) >= limit:
            break
    return rows


async def _active_sub(session: AsyncSession, user_id: int, now: datetime) -> Subscription | None:
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE)
        .order_by(Subscription.created_at.desc())
    )
    for sub in result.scalars():
        if sub.expires_at is None or sub.expires_at > now:
            return sub
    return None


# --- admin actions ---------------------------------------------------------


async def grant_subscription(
    session: AsyncSession, telegram_id: int, tariff: str, *, days: int = 30, now: datetime | None = None
) -> Subscription:
    if tariff not in PLANS:
        raise ValueError(f"unknown tariff {tariff}")
    now = _naive_utc(now)
    user = await _get_or_create(session, telegram_id)
    sub = Subscription(
        user_id=user.id,
        tariff=tariff,
        status=SubscriptionStatus.ACTIVE,
        payment_provider="admin",
        started_at=now,
        expires_at=now + timedelta(days=days),
    )
    session.add(sub)
    await session.flush()
    # An admin grant is a subscription like any other: one user, one plan.
    # Without this it would sit alongside whatever the user already had, and
    # which one applied would come down to sort order.
    retired = await queries.supersede_other_active(
        session, user_id=user.id, keep_id=sub.id, now=now.replace(tzinfo=timezone.utc)
    )
    for old in retired:
        if old.auto_renew and old.order_reference:
            # Deliberately not cancelled here: the admin panel has no payment
            # credentials, and doing it silently from a support tool is worse
            # than saying it out loud.
            logger.warning(
                "admin grant retired subscription %s, whose recurring payment (%s) is still live",
                old.id, old.order_reference,
            )
    return sub


async def revoke_subscription(session: AsyncSession, telegram_id: int, *, now: datetime | None = None) -> int:
    now = _naive_utc(now)
    user = await _get_user(session, telegram_id)
    if user is None:
        return 0
    result = await session.execute(
        select(Subscription).where(
            Subscription.user_id == user.id, Subscription.status == SubscriptionStatus.ACTIVE
        )
    )
    count = 0
    for sub in result.scalars():
        sub.status = SubscriptionStatus.CANCELLED
        count += 1
        if sub.auto_renew and sub.order_reference:
            # Revoking here stops the access, not the billing. The panel holds
            # no payment credentials, so this has to be said out loud rather
            # than left for someone to discover on next month's statement.
            logger.warning(
                "admin revoked subscription %s, whose recurring payment (%s) is still live — "
                "cancel it in the gateway or the card keeps being charged",
                sub.id, sub.order_reference,
            )
    await session.flush()
    return count


async def set_blocked(session: AsyncSession, telegram_id: int, blocked: bool) -> bool:
    user = await _get_user(session, telegram_id)
    if user is None:
        return False
    user.is_blocked = blocked
    await session.flush()
    return True


async def delete_user(session: AsyncSession, telegram_id: int) -> bool:
    """Permanently remove a user and everything tied to them (subscriptions,
    sessions, settings, ignored chats, saved messages, ...) — every FK to
    users.id is ON DELETE CASCADE at the DB level, so deleting the row is
    enough. Irreversible; the caller (admin panel) must confirm first."""
    user = await _get_user(session, telegram_id)
    if user is None:
        return False
    await session.delete(user)
    await session.flush()
    return True


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()


async def _get_or_create(session: AsyncSession, telegram_id: int) -> User:
    user = await _get_user(session, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id)
        session.add(user)
        await session.flush()
    return user


# --- referral links ----------------------------------------------------


_REFERRAL_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def create_referral_link(
    session: AsyncSession, code: str, *, label: str | None = None, discount_percent: int = 0
) -> ReferralLink:
    # Telegram's own /start deep-link payload is restricted to this exact
    # charset (max 64 chars) — a code outside it would produce a ref_<code>
    # link Telegram itself won't accept, so reject it up front rather than
    # `str.isalnum()` (Unicode-aware — would wrongly allow e.g. Cyrillic).
    if not _REFERRAL_CODE_RE.match(code):
        raise ValueError("code must be ASCII letters/digits/_/- , 1-64 chars")
    if not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be between 0 and 100")
    existing = await referrals.get_link_by_code(session, code)
    if existing is not None:
        raise ValueError(f"referral code {code!r} already exists")
    return await referrals.create_link(session, code, label=label, discount_percent=discount_percent)


async def list_referral_links(session: AsyncSession) -> list[ReferralLinkStats]:
    return await referrals.list_links_with_stats(session)


async def update_referral_discount(session: AsyncSession, code: str, discount_percent: int) -> None:
    if not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be between 0 and 100")
    if not await referrals.update_discount(session, code, discount_percent):
        raise ValueError(f"referral code {code!r} not found")
