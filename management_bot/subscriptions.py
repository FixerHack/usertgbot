"""Subscription purchase/activation, decoupled from the payment gateway.

`purchase` runs the full create-invoice -> check-payment -> activate cycle
against any PaymentProvider, so swapping StubPayment for a real gateway needs
no changes here or in the handler.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PaymentEvent, Subscription, SubscriptionStatus
from management_bot.payment.base import PaymentProvider
from management_bot.storage import upsert_user
from shared.tariffs import Tariff, get_plan

DEFAULT_PERIOD_DAYS = 30

# Our own order-reference format. The subscription id is embedded rather than
# looked up in a side table because a gateway is free to decorate the
# reference it echoes back (a regular payment may report a renewal under a
# suffixed reference); parsing the id out survives that, an exact-match lookup
# would not.
_ORDER_REFERENCE_RE = re.compile(r"^sub-(\d+)-")


def build_order_reference(sub_id: int, *, now: float | None = None) -> str:
    return f"sub-{sub_id}-{int(now if now is not None else time.time())}"


def parse_order_reference(reference: str) -> int | None:
    match = _ORDER_REFERENCE_RE.match(reference or "")
    return int(match.group(1)) if match else None


def _naive_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.utcnow()
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


async def create_pending(
    session: AsyncSession,
    *,
    telegram_id: int,
    tariff: Tariff,
    provider_name: str,
    external_invoice_id: str | None = None,
    username: str | None = None,
    full_name: str | None = None,
    period_days: int = DEFAULT_PERIOD_DAYS,
    auto_renew: bool = False,
    amount_uah: int | None = None,
) -> Subscription:
    """A PENDING subscription awaiting payment confirmation."""
    user = await upsert_user(session, telegram_id, username=username, full_name=full_name)
    sub = Subscription(
        user_id=user.id,
        tariff=tariff.value,
        status=SubscriptionStatus.PENDING,
        payment_provider=provider_name,
        external_invoice_id=external_invoice_id,
        period_days=period_days,
        auto_renew=auto_renew,
        amount_uah=amount_uah,
    )
    session.add(sub)
    await session.flush()
    return sub


async def get_subscription(session: AsyncSession, sub_id: int) -> Subscription | None:
    return (await session.execute(select(Subscription).where(Subscription.id == sub_id))).scalar_one_or_none()


async def activate(
    session: AsyncSession, sub: Subscription, *, now: datetime | None = None, period_days: int | None = None
) -> Subscription:
    """`period_days` defaults to whatever was set on the row at
    `create_pending` time (the duration the buyer actually picked) — a
    Stars invoice payload can't carry it, so it has to round-trip via the
    row itself. Pass it explicitly only to override that."""
    now = _naive_utc(now)
    days = period_days if period_days is not None else (sub.period_days or DEFAULT_PERIOD_DAYS)
    sub.status = SubscriptionStatus.ACTIVE
    sub.started_at = now
    sub.expires_at = now + timedelta(days=days)
    await session.flush()
    return sub


async def get_by_order_reference(session: AsyncSession, reference: str) -> Subscription | None:
    """Resolve the subscription a gateway callback is talking about.

    Tries the reference verbatim first, then falls back to the id embedded in
    it — see `_ORDER_REFERENCE_RE`. The fallback still checks that the stored
    reference is a prefix of the incoming one, so an id that merely happens to
    parse cannot claim someone else's subscription.
    """
    sub = (
        await session.execute(select(Subscription).where(Subscription.order_reference == reference))
    ).scalar_one_or_none()
    if sub is not None:
        return sub

    sub_id = parse_order_reference(reference)
    if sub_id is None:
        return None
    sub = await get_subscription(session, sub_id)
    if sub is None or not sub.order_reference or not reference.startswith(sub.order_reference):
        return None
    return sub


async def record_payment_event(
    session: AsyncSession,
    *,
    provider: str,
    order_reference: str,
    auth_code: str,
    processing_date: str,
    amount: float,
    currency: str,
    status: str,
    subscription_id: int | None = None,
) -> PaymentEvent | None:
    """Insert the charge, or return None if this exact charge was already seen.

    The unique constraint is the check — asking first and inserting second
    would let two concurrent deliveries of the same callback both pass. The
    nested transaction keeps the expected IntegrityError from poisoning the
    outer one.
    """
    event = PaymentEvent(
        provider=provider,
        order_reference=order_reference,
        auth_code=auth_code,
        processing_date=processing_date,
        amount=amount,
        currency=currency,
        status=status,
        subscription_id=subscription_id,
    )
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        return None
    return event


async def extend(
    session: AsyncSession, sub: Subscription, *, days: int | None = None, now: datetime | None = None
) -> Subscription:
    """Add another period to an ALREADY active subscription (a renewal).

    Counts from the current expiry, not from now, so a renewal that lands a
    day early doesn't silently shorten the paid period. An expired one starts
    from now instead.
    """
    now = _naive_utc(now)
    days = days if days is not None else (sub.period_days or DEFAULT_PERIOD_DAYS)
    base = sub.expires_at if sub.expires_at is not None and sub.expires_at > now else now
    sub.status = SubscriptionStatus.ACTIVE
    if sub.started_at is None:
        sub.started_at = now
    sub.expires_at = base + timedelta(days=days)
    await session.flush()
    return sub


async def purchase(
    session: AsyncSession,
    *,
    telegram_id: int,
    tariff: Tariff,
    provider: PaymentProvider,
    now: datetime | None = None,
    period_days: int = DEFAULT_PERIOD_DAYS,
    username: str | None = None,
    full_name: str | None = None,
) -> Subscription:
    """Create and (if payment succeeds) activate a subscription for the user."""
    # DB timestamp columns are naive; store naive UTC (read side treats stored
    # values as UTC). Aware inputs are normalized so asyncpg won't reject them.
    now = now or datetime.utcnow()
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    plan = get_plan(tariff)
    if not plan.available:
        raise ValueError(f"tariff {tariff.value} is not purchasable")

    user = await upsert_user(session, telegram_id, username=username, full_name=full_name)
    provider_name = getattr(provider, "name", type(provider).__name__.lower())

    sub = Subscription(
        user_id=user.id,
        tariff=tariff.value,
        status=SubscriptionStatus.PENDING,
        payment_provider=provider_name,
    )
    session.add(sub)
    await session.flush()

    invoice = await provider.create_invoice(user.id, tariff.value, plan.profit_uah, "UAH")
    sub.external_invoice_id = invoice.id

    payment = await provider.check_payment(invoice.id)
    if payment.paid:
        sub.status = SubscriptionStatus.ACTIVE
        sub.started_at = now
        sub.expires_at = now + timedelta(days=period_days)
    await session.flush()
    return sub
