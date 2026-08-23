"""Userbot command gating (DB) + recent-message cache (in-memory)."""

from datetime import datetime, timedelta, timezone

from db.models import Subscription, SubscriptionStatus
from management_bot.storage import upsert_user
from userbot.gating import check_command
from userbot.message_cache import RecentMessageCache


# --- gating ---------------------------------------------------------------


async def _user_with_sub(db_session, tariff, status=SubscriptionStatus.ACTIVE, expires=None):
    user = await upsert_user(db_session, 1001)
    db_session.add(
        Subscription(
            user_id=user.id,
            tariff=tariff,
            status=status,
            payment_provider="stub",
            expires_at=expires,
        )
    )
    await db_session.flush()
    return user


async def test_gate_no_subscription(db_session):
    user = await upsert_user(db_session, 1001)
    gate = await check_command(db_session, user.id, "info")
    assert not gate.allowed and gate.reason == "no_subscription"


async def test_gate_standard_allows_base_command(db_session):
    user = await _user_with_sub(db_session, "standard")
    assert (await check_command(db_session, user.id, "info")).allowed


async def test_gate_standard_denies_autoresponder(db_session):
    user = await _user_with_sub(db_session, "standard")
    gate = await check_command(db_session, user.id, "autoresponder")
    assert not gate.allowed and gate.reason == "tariff_excludes"


async def test_gate_pro_allows_autoresponder(db_session):
    user = await _user_with_sub(db_session, "pro")
    assert (await check_command(db_session, user.id, "autoresponder")).allowed


async def test_gate_ignores_expired(db_session):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    user = await _user_with_sub(db_session, "pro", expires=now - timedelta(days=1))
    assert not (await check_command(db_session, user.id, "info", now=now)).allowed


# --- cache ----------------------------------------------------------------


def test_cache_remember_and_pop():
    cache = RecentMessageCache()
    cache.remember(chat_id=10, message_id=1, sender_id=5, text="hello")
    got = cache.get(10, 1)
    assert got is not None and got.text == "hello" and got.sender_id == 5
    popped = cache.pop(10, 1)
    assert popped.text == "hello"
    assert cache.get(10, 1) is None


def test_cache_eviction():
    cache = RecentMessageCache(max_size=3)
    for i in range(5):
        cache.remember(chat_id=1, message_id=i, sender_id=None, text=str(i))
    assert len(cache) == 3
    assert cache.get(1, 0) is None  # oldest evicted
    assert cache.get(1, 4) is not None


def test_pop_by_message_id_resolves_unknown_chat():
    # Telethon's MessageDeleted has chat_id=None for DMs/small groups — this
    # is the fallback path that makes deletion capture work there anyway.
    cache = RecentMessageCache()
    cache.remember(chat_id=555, message_id=42, sender_id=7, text="secret")
    found = cache.pop_by_message_id(42)
    assert found is not None
    assert found.chat_id == 555 and found.text == "secret"
    # popped once -> gone from both indexes
    assert cache.pop_by_message_id(42) is None
    assert cache.get(555, 42) is None


def test_pop_by_message_id_prefers_most_recent_on_collision():
    cache = RecentMessageCache()
    cache.remember(chat_id=1, message_id=99, sender_id=1, text="first chat")
    cache.remember(chat_id=2, message_id=99, sender_id=2, text="second chat")  # same id, different chat
    found = cache.pop_by_message_id(99)
    assert found.chat_id == 2 and found.text == "second chat"


def test_pop_by_message_id_unknown_returns_none():
    cache = RecentMessageCache()
    assert cache.pop_by_message_id(12345) is None
