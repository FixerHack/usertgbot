"""Storage layer against in-memory SQLite: upsert, encrypt-at-rest, status."""

from datetime import datetime, timezone

from cryptography.fernet import Fernet

from conftest import TEST_ENCRYPTION_KEY
from db.models import Subscription, SubscriptionStatus
from management_bot import storage


def test_normalize_phone():
    assert storage.normalize_phone("+380 (67) 123-45-67") == "+380671234567"
    assert storage.normalize_phone("15551234567") == "+15551234567"
    assert storage.normalize_phone("") == ""


async def test_save_session_encrypts_at_rest(db_session):
    row = await storage.save_session(
        db_session,
        telegram_id=1001,
        phone_number="+15551234567",
        session_string="SECRET-SESSION",
        username="bob",
        full_name="Bob Ross",
    )
    await db_session.commit()

    # Stored bytes are ciphertext, not the plaintext session.
    assert row.encrypted_session != b"SECRET-SESSION"
    assert b"SECRET-SESSION" not in row.encrypted_session
    decrypted = Fernet(TEST_ENCRYPTION_KEY.encode()).decrypt(row.encrypted_session).decode()
    assert decrypted == "SECRET-SESSION"


async def test_save_session_upserts_same_phone(db_session):
    first = await storage.save_session(
        db_session, telegram_id=1001, phone_number="+15551234567", session_string="one"
    )
    second = await storage.save_session(
        db_session, telegram_id=1001, phone_number="+15551234567", session_string="two"
    )
    await db_session.commit()

    assert first.id == second.id  # replaced, not duplicated
    decrypted = Fernet(TEST_ENCRYPTION_KEY.encode()).decrypt(second.encrypted_session).decode()
    assert decrypted == "two"


async def test_upsert_user_updates_profile(db_session):
    u1 = await storage.upsert_user(db_session, 1001, username="old", full_name="Old Name")
    u2 = await storage.upsert_user(db_session, 1001, username="new")
    await db_session.commit()

    assert u1.id == u2.id
    assert u2.username == "new"
    assert u2.full_name == "Old Name"  # untouched when not provided


async def test_get_status_unknown_user(db_session):
    status = await storage.get_user_status(db_session, 42)
    assert status.known is False
    assert status.subscription is None
    assert status.sessions == []


async def test_get_status_with_sub_and_session(db_session):
    user = await storage.upsert_user(db_session, 1001, username="bob")
    db_session.add(
        Subscription(
            user_id=user.id,
            tariff="pro",
            status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    )
    await storage.save_session(
        db_session, telegram_id=1001, phone_number="+15551234567", session_string="sess"
    )
    await db_session.commit()

    status = await storage.get_user_status(db_session, 1001)
    assert status.known is True
    assert status.subscription.tariff == "pro"
    assert status.subscription.status == "active"
    assert len(status.sessions) == 1
    assert status.sessions[0].phone_number == "+15551234567"


async def test_get_status_prefers_active_subscription(db_session):
    user = await storage.upsert_user(db_session, 1001)
    db_session.add_all(
        [
            Subscription(user_id=user.id, tariff="standard", status=SubscriptionStatus.EXPIRED, payment_provider="stub"),
            Subscription(user_id=user.id, tariff="pro", status=SubscriptionStatus.ACTIVE, payment_provider="stub"),
        ]
    )
    await db_session.commit()

    status = await storage.get_user_status(db_session, 1001)
    assert status.subscription.tariff == "pro"


async def test_get_last_known_phone_none_for_new_user(db_session):
    assert await storage.get_last_known_phone(db_session, 9001) is None


async def test_get_last_known_phone_from_active_session(db_session):
    await storage.save_session(db_session, telegram_id=1001, phone_number="+15551234567", session_string="s")
    await db_session.commit()

    assert await storage.get_last_known_phone(db_session, 1001) == "+15551234567"


async def test_get_last_known_phone_survives_unlink(db_session):
    """A deactivated (unlinked / dead-session) row must still answer the
    question — that's the whole point: re-link shouldn't have to ask again."""
    from db.queries import deactivate_session

    row = await storage.save_session(db_session, telegram_id=1001, phone_number="+15551234567", session_string="s")
    await db_session.commit()
    await deactivate_session(db_session, row.id)
    await db_session.commit()

    assert await storage.get_last_known_phone(db_session, 1001) == "+15551234567"


async def test_get_last_known_phone_picks_the_most_recent(db_session):
    await storage.save_session(db_session, telegram_id=1001, phone_number="+1old", session_string="a")
    await db_session.commit()
    await storage.save_session(db_session, telegram_id=1001, phone_number="+2new", session_string="b")
    await db_session.commit()

    assert await storage.get_last_known_phone(db_session, 1001) == "+2new"


async def test_lapsed_subscription_is_not_reported_as_active(db_session):
    """Nothing sweeps the table to flip ACTIVE to EXPIRED when a period ends,
    so the row keeps saying "active" long after it stopped being true. The
    command gate already checked the date; this screen did not, and told a
    lapsed subscriber they were active while every command was refused."""
    user = await storage.upsert_user(db_session, 2001)
    db_session.add(
        Subscription(
            user_id=user.id,
            tariff="pro",
            status=SubscriptionStatus.ACTIVE,
            payment_provider="wayforpay",
            expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()

    status = await storage.get_user_status(db_session, 2001)
    assert status.subscription.status == "expired"


async def test_a_live_subscription_wins_over_a_lapsed_one(db_session):
    """A renewal or a re-purchase leaves the old row behind; the screen has to
    show the one the user is actually on."""
    user = await storage.upsert_user(db_session, 2002)
    db_session.add_all(
        [
            Subscription(
                user_id=user.id, tariff="standard", status=SubscriptionStatus.ACTIVE,
                payment_provider="wayforpay",
                expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            ),
            Subscription(
                user_id=user.id, tariff="premium", status=SubscriptionStatus.ACTIVE,
                payment_provider="wayforpay",
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db_session.commit()

    status = await storage.get_user_status(db_session, 2002)
    assert status.subscription.tariff == "premium"
    assert status.subscription.status == "active"


async def test_a_subscription_with_no_expiry_stays_active(db_session):
    """A granted/comped subscription has no end date; it must not be read as
    "expired at the epoch"."""
    user = await storage.upsert_user(db_session, 2003)
    db_session.add(
        Subscription(
            user_id=user.id, tariff="pro", status=SubscriptionStatus.ACTIVE,
            payment_provider="referral_free", expires_at=None,
        )
    )
    await db_session.commit()

    status = await storage.get_user_status(db_session, 2003)
    assert status.subscription.status == "active"


async def test_a_lapsed_subscription_beats_an_abandoned_checkout(db_session):
    """Every tap on "buy" that is never paid leaves a PENDING row behind. If
    the newest row simply won, someone whose Pro had just run out would be
    told they are awaiting payment — describing a checkout they walked away
    from rather than the subscription they lost."""
    user = await storage.upsert_user(db_session, 2004)
    db_session.add_all(
        [
            Subscription(
                user_id=user.id, tariff="pro", status=SubscriptionStatus.ACTIVE,
                payment_provider="wayforpay",
                started_at=datetime(2026, 8, 1),
                expires_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            ),
            Subscription(
                user_id=user.id, tariff="pro", status=SubscriptionStatus.PENDING,
                payment_provider="wayforpay",
            ),
        ]
    )
    await db_session.commit()

    status = await storage.get_user_status(db_session, 2004)
    assert status.subscription.status == "expired"
    assert status.subscription.expires_at is not None


async def test_a_first_time_buyer_mid_checkout_still_sees_pending(db_session):
    """The rule must not hide the only thing a brand-new user has."""
    user = await storage.upsert_user(db_session, 2005)
    db_session.add(
        Subscription(
            user_id=user.id, tariff="pro", status=SubscriptionStatus.PENDING,
            payment_provider="wayforpay",
        )
    )
    await db_session.commit()

    status = await storage.get_user_status(db_session, 2005)
    assert status.subscription.status == "pending"
