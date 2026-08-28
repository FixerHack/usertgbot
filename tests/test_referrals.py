"""Referral links: attribution, discount lookup, click/conversion stats."""

from datetime import datetime

from db.models import Subscription, SubscriptionStatus
from management_bot.storage import upsert_user
from shared import referrals

NOW = datetime(2026, 8, 28)


async def test_create_and_lookup_link(db_session):
    link = await referrals.create_link(db_session, "youtube", label="YouTube promo", discount_percent=10)
    assert link.id is not None

    got = await referrals.get_link_by_code(db_session, "youtube")
    assert got is not None and got.id == link.id and got.discount_percent == 10

    assert await referrals.get_link_by_code(db_session, "unknown") is None


async def test_attribute_user_sets_once(db_session):
    link_a = await referrals.create_link(db_session, "a")
    link_b = await referrals.create_link(db_session, "b")
    user = await upsert_user(db_session, 1001)

    assert await referrals.attribute_user(db_session, user, "a") is True
    assert user.referred_by_link_id == link_a.id

    # already attributed -> second attempt is a no-op, even for a different link
    assert await referrals.attribute_user(db_session, user, "b") is False
    assert user.referred_by_link_id == link_a.id
    assert link_b.id != link_a.id


async def test_attribute_user_unknown_code(db_session):
    user = await upsert_user(db_session, 1002)
    assert await referrals.attribute_user(db_session, user, "does-not-exist") is False
    assert user.referred_by_link_id is None


async def test_get_discount_percent(db_session):
    link = await referrals.create_link(db_session, "d", discount_percent=15)
    user = await upsert_user(db_session, 1003)
    await referrals.attribute_user(db_session, user, "d")

    assert await referrals.get_discount_percent(db_session, 1003) == 15
    # a user with no referral gets no discount
    assert await referrals.get_discount_percent(db_session, 9999) == 0


async def test_update_discount(db_session):
    await referrals.create_link(db_session, "e", discount_percent=5)
    assert await referrals.update_discount(db_session, "e", 25) is True
    assert await referrals.get_discount_percent(db_session, 999) == 0  # unrelated user, sanity check
    got = await referrals.get_link_by_code(db_session, "e")
    assert got.discount_percent == 25

    assert await referrals.update_discount(db_session, "does-not-exist", 10) is False


async def test_list_links_with_stats(db_session):
    link = await referrals.create_link(db_session, "promo", label="Promo", discount_percent=5)
    other = await referrals.create_link(db_session, "other")

    u1 = await upsert_user(db_session, 2001)
    u2 = await upsert_user(db_session, 2002)
    u3 = await upsert_user(db_session, 2003)
    await referrals.attribute_user(db_session, u1, "promo")
    await referrals.attribute_user(db_session, u2, "promo")
    await referrals.attribute_user(db_session, u3, "other")

    # u1 bought standard (active), u2's subscription is only pending (doesn't count)
    db_session.add(Subscription(
        user_id=u1.id, tariff="standard", status=SubscriptionStatus.ACTIVE,
        payment_provider="stars", started_at=NOW,
    ))
    db_session.add(Subscription(
        user_id=u2.id, tariff="pro", status=SubscriptionStatus.PENDING, payment_provider="stars",
    ))
    await db_session.flush()

    stats = {s.code: s for s in await referrals.list_links_with_stats(db_session)}
    assert stats["promo"].clicks == 2
    assert stats["promo"].discount_percent == 5
    assert stats["promo"].by_tariff == {"standard": 1}
    assert stats["other"].clicks == 1
    assert stats["other"].by_tariff == {}
