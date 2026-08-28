"""Subscription purchase/activation, decoupled from the payment gateway.

`purchase` runs the full create-invoice -> check-payment -> activate cycle
against any PaymentProvider, so swapping StubPayment for a real gateway needs
no changes here or in the handler.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Subscription, SubscriptionStatus
from management_bot.payment.base import PaymentProvider
from management_bot.storage import upsert_user
from shared.tariffs import Tariff, get_plan

DEFAULT_PERIOD_DAYS = 30


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
) -> Subscription:
    """A PENDING subscription awaiting payment confirmation (Stars/crypto)."""
    user = await upsert_user(session, telegram_id, username=username, full_name=full_name)
    sub = Subscription(
        user_id=user.id,
        tariff=tariff.value,
        status=SubscriptionStatus.PENDING,
        payment_provider=provider_name,
        external_invoice_id=external_invoice_id,
        period_days=period_days,
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
