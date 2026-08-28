"""Referral links: admin-issued tracking links (`/start ref_<code>`).

Attribute a new user to a source (set once, on their first /start with the
payload — never overwritten afterward) and can carry a discount applied at
purchase time. Links never grant a subscription by themselves — only
tracking (how many people came through) plus an optional price break for
whoever did.

Session-injected, like management_bot/storage.py and admin_web/service.py —
usable from both management_bot (attribution + discount lookup at purchase
time) and admin_web (create/list links + stats).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ReferralLink, Subscription, SubscriptionStatus, User


async def create_link(
    session: AsyncSession, code: str, *, label: str | None = None, discount_percent: int = 0
) -> ReferralLink:
    link = ReferralLink(code=code, label=label, discount_percent=discount_percent)
    session.add(link)
    await session.flush()
    return link


async def get_link_by_code(session: AsyncSession, code: str) -> ReferralLink | None:
    return (await session.execute(select(ReferralLink).where(ReferralLink.code == code))).scalar_one_or_none()


async def update_discount(session: AsyncSession, code: str, discount_percent: int) -> bool:
    link = await get_link_by_code(session, code)
    if link is None:
        return False
    link.discount_percent = discount_percent
    await session.flush()
    return True


async def attribute_user(session: AsyncSession, user: User, code: str) -> bool:
    """Set `user.referred_by_link_id` if not already set and `code` exists.
    Returns whether attribution happened."""
    if user.referred_by_link_id is not None:
        return False
    link = await get_link_by_code(session, code)
    if link is None:
        return False
    user.referred_by_link_id = link.id
    await session.flush()
    return True


async def get_discount_percent(session: AsyncSession, telegram_id: int) -> int:
    result = await session.execute(
        select(ReferralLink.discount_percent)
        .join(User, User.referred_by_link_id == ReferralLink.id)
        .where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none() or 0


@dataclass
class ReferralLinkStats:
    code: str
    label: str | None
    discount_percent: int
    clicks: int
    by_tariff: dict[str, int] = field(default_factory=dict)


async def list_links_with_stats(session: AsyncSession) -> list[ReferralLinkStats]:
    links = (await session.execute(select(ReferralLink).order_by(ReferralLink.created_at.desc()))).scalars().all()
    if not links:
        return []

    clicks_rows = await session.execute(
        select(User.referred_by_link_id, func.count())
        .where(User.referred_by_link_id.isnot(None))
        .group_by(User.referred_by_link_id)
    )
    clicks_by_link = dict(clicks_rows.all())

    # ACTIVE subscriptions of referred users, grouped by (link, tariff) —
    # same "active only" convention as admin_web.service.get_metrics.
    tariff_rows = await session.execute(
        select(User.referred_by_link_id, Subscription.tariff, func.count())
        .join(Subscription, Subscription.user_id == User.id)
        .where(User.referred_by_link_id.isnot(None), Subscription.status == SubscriptionStatus.ACTIVE)
        .group_by(User.referred_by_link_id, Subscription.tariff)
    )
    by_tariff_by_link: dict[int, dict[str, int]] = {}
    for link_id, tariff, count in tariff_rows.all():
        by_tariff_by_link.setdefault(link_id, {})[tariff] = count

    return [
        ReferralLinkStats(
            code=link.code,
            label=link.label,
            discount_percent=link.discount_percent,
            clicks=clicks_by_link.get(link.id, 0),
            by_tariff=by_tariff_by_link.get(link.id, {}),
        )
        for link in links
    ]
