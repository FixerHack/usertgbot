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


# --- gateway order references ---------------------------------------------


async def test_order_reference_round_trips_through_its_id():
    from management_bot.subscriptions import build_order_reference, parse_order_reference

    assert parse_order_reference(build_order_reference(42)) == 42
    assert parse_order_reference("not-ours-42") is None
    assert parse_order_reference("") is None


async def test_a_reference_that_merely_parses_cannot_claim_a_subscription(db_session):
    """The id is embedded in the reference, so anything that looks like ours
    parses. Resolution still has to prove the stored reference is a prefix of
    the incoming one, or one subscription's callback could credit another."""
    from management_bot import subscriptions
    from shared.tariffs import Tariff

    sub = await subscriptions.create_pending(
        db_session, telegram_id=1, tariff=Tariff.PRO, provider_name="wayforpay"
    )
    sub.order_reference = subscriptions.build_order_reference(sub.id, now=1700000000)
    await db_session.commit()

    assert await subscriptions.get_by_order_reference(db_session, sub.order_reference) is sub
    # same id, different (forged) timestamp
    assert await subscriptions.get_by_order_reference(db_session, f"sub-{sub.id}-1699999999") is None


# --- changing plan mid-period ----------------------------------------------


class _Canceller:
    """Stands in for the gateway call that stops a recurring payment."""

    def __init__(self, fail: bool = False):
        self.calls: list[str] = []
        self.fail = fail

    async def __call__(self, order_reference: str) -> None:
        self.calls.append(order_reference)
        if self.fail:
            raise RuntimeError("gateway said no")


async def _sub(db, telegram_id, tariff, *, days=30, active=False, now=None, auto_renew=False, ref=None):
    from management_bot import subscriptions

    sub = await subscriptions.create_pending(
        db, telegram_id=telegram_id, tariff=tariff, provider_name="wayforpay",
        period_days=days, auto_renew=auto_renew,
    )
    sub.order_reference = ref
    if active:
        await subscriptions.activate(db, sub, now=now, mandate_canceller=_Canceller())
    await db.commit()
    return sub


async def test_buying_pro_mid_standard_replaces_it(db_session):
    """One user, one plan. Overlapping subscriptions leave it to sort order to
    decide which one applies, which is not something a paying customer should
    be exposed to."""
    from management_bot import subscriptions

    start = datetime(2026, 9, 1)
    standard = await _sub(db_session, 4001, Tariff.STANDARD, active=True, now=start)

    pro = await subscriptions.create_pending(
        db_session, telegram_id=4001, tariff=Tariff.PRO, provider_name="wayforpay", period_days=30
    )
    canceller = _Canceller()
    await subscriptions.activate(db_session, pro, now=datetime(2026, 9, 15), mandate_canceller=canceller)
    await db_session.commit()

    await db_session.refresh(standard)
    assert standard.status is SubscriptionStatus.CANCELLED
    assert pro.status is SubscriptionStatus.ACTIVE
    # a full period from the moment of purchase, no proration either way
    assert pro.started_at == datetime(2026, 9, 15)
    assert pro.expires_at == datetime(2026, 10, 15)


async def test_the_gate_sees_exactly_one_plan_after_a_change(db_session):
    from db import queries
    from management_bot import subscriptions, storage

    await _sub(db_session, 4002, Tariff.STANDARD, active=True, now=datetime(2026, 9, 1))
    pro = await subscriptions.create_pending(
        db_session, telegram_id=4002, tariff=Tariff.PRO, provider_name="wayforpay", period_days=30
    )
    await subscriptions.activate(db_session, pro, now=datetime(2026, 9, 15), mandate_canceller=_Canceller())
    await db_session.commit()

    user = await storage.upsert_user(db_session, 4002)
    active = await queries.get_active_subscription_for_user(db_session, user.id)
    assert active is not None and active.tariff == "pro"


async def test_rebuying_the_same_plan_keeps_the_days_already_paid_for(db_session):
    """Replacing is right for a tariff CHANGE; applied to the same plan it
    would quietly burn whatever was left of the month."""
    from management_bot import subscriptions

    old = await _sub(db_session, 4003, Tariff.PRO, active=True, now=datetime(2026, 9, 1))
    assert old.expires_at == datetime(2026, 10, 1)

    again = await subscriptions.create_pending(
        db_session, telegram_id=4003, tariff=Tariff.PRO, provider_name="wayforpay", period_days=30
    )
    await subscriptions.activate(db_session, again, now=datetime(2026, 9, 21), mandate_canceller=_Canceller())
    await db_session.commit()

    # 30 new days plus the 10 still owed on the old one: the old period ran to
    # 1 Oct, so a month bought on 21 Sep must land on 31 Oct, not 21 Oct.
    assert again.expires_at == datetime(2026, 10, 31)


async def test_a_superseded_card_mandate_is_cancelled(db_session):
    """The expensive half: a retired plan whose recurring payment keeps
    charging bills someone twice for one subscription."""
    from management_bot import subscriptions

    await _sub(
        db_session, 4004, Tariff.STANDARD, active=True, now=datetime(2026, 9, 1),
        auto_renew=True, ref="sub-1-1700",
    )
    pro = await subscriptions.create_pending(
        db_session, telegram_id=4004, tariff=Tariff.PRO, provider_name="wayforpay", period_days=30
    )
    canceller = _Canceller()
    await subscriptions.activate(db_session, pro, now=datetime(2026, 9, 15), mandate_canceller=canceller)
    await db_session.commit()

    assert canceller.calls == ["sub-1-1700"]


async def test_a_mandate_we_failed_to_cancel_stays_flagged(db_session):
    """Clearing the flag on a gateway failure would hide a card that is still
    being charged — the same rule the cancel button follows."""
    from management_bot import subscriptions

    standard = await _sub(
        db_session, 4005, Tariff.STANDARD, active=True, now=datetime(2026, 9, 1),
        auto_renew=True, ref="sub-2-1700",
    )
    pro = await subscriptions.create_pending(
        db_session, telegram_id=4005, tariff=Tariff.PRO, provider_name="wayforpay", period_days=30
    )
    await subscriptions.activate(
        db_session, pro, now=datetime(2026, 9, 15), mandate_canceller=_Canceller(fail=True)
    )
    await db_session.commit()

    await db_session.refresh(standard)
    assert standard.status is SubscriptionStatus.CANCELLED
    assert standard.auto_renew is True, "still live at the gateway, so still flagged here"


async def test_an_already_expired_plan_is_left_alone(db_session):
    """Retiring history rewrites what a user's account says happened."""
    from management_bot import subscriptions

    old = await _sub(db_session, 4006, Tariff.STANDARD, active=True, now=datetime(2026, 1, 1))
    pro = await subscriptions.create_pending(
        db_session, telegram_id=4006, tariff=Tariff.PRO, provider_name="wayforpay", period_days=30
    )
    await subscriptions.activate(db_session, pro, now=datetime(2026, 9, 15), mandate_canceller=_Canceller())
    await db_session.commit()

    await db_session.refresh(old)
    assert old.status is SubscriptionStatus.ACTIVE, "long past its expiry; not ours to relabel"
