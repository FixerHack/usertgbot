"""Cross-service DB query helpers: subscriptions, quota, settings, saved msgs."""

from datetime import date, datetime, timedelta, timezone

from db import queries
from db.models import Subscription, SubscriptionStatus
from management_bot.storage import upsert_user


async def _user(db_session, telegram_id=1001):
    user = await upsert_user(db_session, telegram_id)
    return user


async def test_active_subscription_respects_expiry(db_session):
    user = await _user(db_session)
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id,
            tariff="pro",
            status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=now - timedelta(days=1),  # expired
        )
    )
    await db_session.flush()
    assert await queries.get_active_subscription_for_user(db_session, user.id, now=now) is None

    db_session.add(
        Subscription(
            user_id=user.id,
            tariff="pro",
            status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=now + timedelta(days=5),  # valid
        )
    )
    await db_session.flush()
    active = await queries.get_active_subscription_for_user(db_session, user.id, now=now)
    assert active is not None and active.tariff == "pro"


async def test_consume_check_enforces_quota(db_session):
    user = await _user(db_session)
    today = date(2026, 8, 22)
    results = [await queries.consume_check(db_session, user.id, 3, today=today) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[2].remaining == 0
    assert results[3].used == 3  # not incremented past the cap


async def test_check_quota_resets_next_month(db_session):
    user = await _user(db_session)
    await queries.consume_check(db_session, user.id, 5, today=date(2026, 8, 31))
    await queries.consume_check(db_session, user.id, 5, today=date(2026, 8, 31))
    peek_aug = await queries.peek_check_quota(db_session, user.id, 5, today=date(2026, 8, 31))
    assert peek_aug.used == 2

    sept = await queries.consume_check(db_session, user.id, 5, today=date(2026, 9, 1))
    assert sept.used == 1  # reset on new month


async def test_settings_roundtrip(db_session):
    user = await _user(db_session)
    await queries.set_me_card(db_session, user.id, {"text": "hi"})
    await queries.set_autoresponder(db_session, user.id, {"enabled": True, "message": "away"})
    row = await queries.get_or_create_settings(db_session, user.id)
    assert row.me_card == {"text": "hi"}
    assert row.autoresponder["enabled"] is True


async def test_ignored_chats_add_list_remove(db_session):
    from db.queries import add_ignored_chat, is_chat_ignored, list_ignored_chats, remove_ignored_chat

    user = await _user(db_session)

    assert await is_chat_ignored(db_session, user.id, 555) is False
    assert await list_ignored_chats(db_session, user.id) == []

    await add_ignored_chat(db_session, user.id, 555, chat_title="Test Group")
    assert await is_chat_ignored(db_session, user.id, 555) is True

    chats = await list_ignored_chats(db_session, user.id)
    assert len(chats) == 1 and chats[0].chat_id == 555 and chats[0].chat_title == "Test Group"

    # adding the same chat again is a no-op, not a duplicate row
    await add_ignored_chat(db_session, user.id, 555)
    assert len(await list_ignored_chats(db_session, user.id)) == 1

    await remove_ignored_chat(db_session, user.id, 555)
    assert await is_chat_ignored(db_session, user.id, 555) is False
    assert await list_ignored_chats(db_session, user.id) == []

    # removing something not present is a safe no-op
    await remove_ignored_chat(db_session, user.id, 999)


async def test_deactivate_user_sessions(db_session):
    from db.queries import deactivate_session, deactivate_user_sessions
    from management_bot.storage import save_session

    user = await _user(db_session)
    await save_session(db_session, telegram_id=1001, phone_number="+111", session_string="a")
    await save_session(db_session, telegram_id=1001, phone_number="+222", session_string="b")
    await db_session.flush()

    count = await deactivate_user_sessions(db_session, user.id)
    assert count == 2

    from sqlalchemy import select

    from db.models import Session

    active = (await db_session.execute(select(Session).where(Session.is_active.is_(True)))).scalars().all()
    assert active == []

    # deactivate_session is a no-op on an unknown id and idempotent otherwise
    await deactivate_session(db_session, 99999)


async def test_media_upsert_and_delete(db_session):
    user = await _user(db_session)
    from db.queries import delete_media, set_media
    from userbot.storage import load_media

    await set_media(db_session, user.id, "me", b"\x89PNG-first", mime="image/png")
    assert await load_media(db_session, user.id, "me") == b"\x89PNG-first"

    await set_media(db_session, user.id, "me", b"second", mime="image/jpeg")
    assert await load_media(db_session, user.id, "me") == b"second"  # upsert, not duplicate

    await delete_media(db_session, user.id, "me")
    assert await load_media(db_session, user.id, "me") is None


async def test_saved_messages(db_session):
    user = await _user(db_session)
    await queries.save_captured_message(
        db_session,
        owner_user_id=user.id,
        chat_id=555,
        message_id=1,
        event_type="deleted",
        text="bye",
    )
    await queries.save_captured_message(
        db_session,
        owner_user_id=user.id,
        chat_id=555,
        message_id=2,
        event_type="edited",
        text="new",
        previous_text="old",
    )
    saved = await queries.list_saved_messages(db_session, user.id)
    assert len(saved) == 2
    assert saved[0].event_type == "edited"  # newest first


# --- .send cooldown ----------------------------------------------------------


async def test_send_cooldown_allows_a_fresh_user(db_session):
    user = await _user(db_session)
    allowed, remaining = await queries.peek_send_cooldown(db_session, user.id, 600)
    assert allowed is True
    assert remaining == 0


async def test_send_cooldown_blocks_until_it_elapses(db_session):
    user = await _user(db_session)
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    await queries.mark_send_used(db_session, user.id, now=now)
    await db_session.flush()

    still_cooling = await queries.peek_send_cooldown(db_session, user.id, 600, now=now + timedelta(seconds=200))
    assert still_cooling[0] is False
    assert still_cooling[1] == 400  # 600 - 200

    just_after = await queries.peek_send_cooldown(db_session, user.id, 600, now=now + timedelta(seconds=601))
    assert just_after == (True, 0)


async def test_send_cooldown_is_per_user(db_session):
    a = await _user(db_session, telegram_id=2001)
    b = await _user(db_session, telegram_id=2002)
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    await queries.mark_send_used(db_session, a.id, now=now)
    await db_session.flush()

    assert (await queries.peek_send_cooldown(db_session, a.id, 600, now=now + timedelta(seconds=10)))[0] is False
    assert (await queries.peek_send_cooldown(db_session, b.id, 600, now=now + timedelta(seconds=10)))[0] is True


# --- .send: cooldown and the hourly cap ------------------------------------


async def test_the_hourly_cap_stops_a_run_the_cooldown_would_allow(db_session):
    """Shortening the cooldown must not quietly become "unlimited, in smaller
    pieces" — that is the whole reason the second limit exists."""
    from datetime import datetime, timedelta, timezone

    from db import queries
    from management_bot import storage

    user = await storage.upsert_user(db_session, 6001)
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)

    # Five runs, each well past the 5-minute cooldown.
    for i in range(5):
        moment = start + timedelta(minutes=6 * i)
        allowance = await queries.peek_send_allowance(
            db_session, user.id, cooldown_seconds=300, max_per_hour=5, now=moment
        )
        assert allowance.allowed, f"run {i} should be inside both limits"
        await queries.mark_send_used(db_session, user.id, now=moment)

    # A sixth, again past the cooldown, but the hour is spent.
    sixth = start + timedelta(minutes=30)
    allowance = await queries.peek_send_allowance(
        db_session, user.id, cooldown_seconds=300, max_per_hour=5, now=sixth
    )
    assert not allowance.allowed
    assert allowance.reason == "hourly"
    assert allowance.remaining_seconds > 0


async def test_the_hour_frees_up_as_the_oldest_run_ages_out(db_session):
    from datetime import datetime, timedelta, timezone

    from db import queries
    from management_bot import storage

    user = await storage.upsert_user(db_session, 6002)
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    for i in range(5):
        await queries.mark_send_used(db_session, user.id, now=start + timedelta(minutes=6 * i))

    just_before = await queries.peek_send_allowance(
        db_session, user.id, cooldown_seconds=300, max_per_hour=5,
        now=start + timedelta(minutes=59),
    )
    assert not just_before.allowed

    just_after = await queries.peek_send_allowance(
        db_session, user.id, cooldown_seconds=300, max_per_hour=5,
        now=start + timedelta(minutes=61),
    )
    assert just_after.allowed, "the first run has left the window"


async def test_the_cooldown_still_reports_itself_separately(db_session):
    """Telling someone to wait four minutes when the hour is spent sends them
    back to fail again."""
    from datetime import datetime, timedelta, timezone

    from db import queries
    from management_bot import storage

    user = await storage.upsert_user(db_session, 6003)
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    await queries.mark_send_used(db_session, user.id, now=start)

    allowance = await queries.peek_send_allowance(
        db_session, user.id, cooldown_seconds=300, max_per_hour=5,
        now=start + timedelta(minutes=1),
    )
    assert not allowance.allowed and allowance.reason == "cooldown"


async def test_send_history_does_not_grow_without_bound(db_session):
    from datetime import datetime, timedelta, timezone

    from db import queries
    from management_bot import storage

    user = await storage.upsert_user(db_session, 6004)
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    for i in range(80):
        await queries.mark_send_used(db_session, user.id, now=start + timedelta(minutes=i))

    row = await queries.get_or_create_settings(db_session, user.id)
    assert len(row.send_history) <= 50


async def test_a_corrupt_history_entry_does_not_lock_anyone_out(db_session):
    """A bad row must not cost someone a feature they paid for."""
    from datetime import datetime, timezone

    from db import queries
    from management_bot import storage

    user = await storage.upsert_user(db_session, 6005)
    row = await queries.get_or_create_settings(db_session, user.id)
    row.send_history = ["not-a-date", "also bad"]
    await db_session.flush()

    allowance = await queries.peek_send_allowance(
        db_session, user.id, cooldown_seconds=300, max_per_hour=5,
        now=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
    )
    assert allowance.allowed
