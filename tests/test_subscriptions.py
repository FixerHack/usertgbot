"""Subscription purchase/activation via the stub gateway."""

from datetime import datetime, timezone

import pytest

from db.models import SubscriptionStatus
from management_bot import subscriptions
from management_bot.payment import StubPayment
from shared.tariffs import Tariff


async def test_purchase_activates_subscription(db_session):
    now = datetime(2026, 8, 22)  # naive UTC, matching the DB timestamp columns
    sub = await subscriptions.purchase(
        db_session,
        telegram_id=1001,
        tariff=Tariff.PRO,
        provider=StubPayment(),
        now=now,
    )
    await db_session.commit()

    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.tariff == "pro"
    assert sub.payment_provider == "stub"
    assert sub.external_invoice_id is not None
    assert sub.started_at == now
    assert sub.expires_at is not None and sub.expires_at > now


async def test_purchase_normalizes_aware_now_to_naive(db_session):
    # aware input must be stored naive so asyncpg accepts it on Postgres
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    sub = await subscriptions.purchase(
        db_session, telegram_id=1002, tariff=Tariff.STANDARD, provider=StubPayment(), now=now
    )
    await db_session.commit()
    assert sub.started_at.tzinfo is None
    assert sub.expires_at.tzinfo is None


async def test_purchase_premium_now_succeeds(db_session):
    """Premium used to be rejected as not-purchasable; it went live with a
    real price, so buying it must work like any other plan."""
    sub = await subscriptions.purchase(
        db_session, telegram_id=1001, tariff=Tariff.PREMIUM, provider=StubPayment()
    )
    assert sub.tariff == "premium"


async def test_purchasing_an_unavailable_plan_is_still_rejected(db_session, monkeypatch):
    """The guard stays live code even with every plan currently available."""
    import dataclasses

    from shared import tariffs as mod

    monkeypatch.setitem(
        mod.PLANS, Tariff.PREMIUM, dataclasses.replace(mod.PLANS[Tariff.PREMIUM], available=False)
    )
    with pytest.raises(ValueError):
        await subscriptions.purchase(
            db_session, telegram_id=1002, tariff=Tariff.PREMIUM, provider=StubPayment()
        )


async def test_purchase_creates_user(db_session):
    from db.queries import get_user_by_telegram_id

    await subscriptions.purchase(
        db_session, telegram_id=2002, tariff=Tariff.STANDARD, provider=StubPayment()
    )
    await db_session.commit()
    user = await get_user_by_telegram_id(db_session, 2002)
    assert user is not None
