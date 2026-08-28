"""Userbot command gating (DB) + recent-message cache (SQLite-backed)."""

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from db.models import Subscription, SubscriptionStatus
from management_bot.storage import upsert_user
from userbot.gating import check_command
from userbot.message_cache import RecentMessageCache


def _cache(owner_user_id: int = 1, max_size: int = 2000) -> RecentMessageCache:
    # ":memory:" — each instance gets its own isolated in-memory DB, no
    # temp files needed for tests
    return RecentMessageCache(owner_user_id=owner_user_id, db_path=":memory:", max_size=max_size)


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


async def test_gate_standard_denies_send(db_session):
    user = await _user_with_sub(db_session, "standard")
    gate = await check_command(db_session, user.id, "send")
    assert not gate.allowed and gate.reason == "tariff_excludes"


async def test_gate_pro_allows_send(db_session):
    user = await _user_with_sub(db_session, "pro")
    assert (await check_command(db_session, user.id, "send")).allowed


async def test_gate_ignores_expired(db_session):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    user = await _user_with_sub(db_session, "pro", expires=now - timedelta(days=1))
    assert not (await check_command(db_session, user.id, "info", now=now)).allowed


# --- cache ----------------------------------------------------------------


async def test_cache_remember_and_pop():
    cache = _cache()
    await cache.remember(chat_id=10, message_id=1, sender_id=5, text="hello")
    got = await cache.get(10, 1)
    assert got is not None and got.text == "hello" and got.sender_id == 5
    popped = await cache.pop(10, 1)
    assert popped.text == "hello"
    assert await cache.get(10, 1) is None


async def test_cache_survives_a_reconnect():
    # the whole point of moving off the old in-memory dict: a fresh
    # RecentMessageCache instance pointed at the SAME file must still see
    # rows written by a previous instance (simulates a process restart)
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        first = RecentMessageCache(owner_user_id=1, db_path=path)
        await first.remember(chat_id=10, message_id=1, sender_id=5, text="survives restart")
        await first.close()

        second = RecentMessageCache(owner_user_id=1, db_path=path)
        got = await second.get(10, 1)
        assert got is not None and got.text == "survives restart"
        await second.close()
    finally:
        os.remove(path)


async def test_cache_eviction():
    cache = _cache(max_size=3)
    for i in range(5):
        await cache.remember(chat_id=1, message_id=i, sender_id=None, text=str(i))
    assert await cache.count() == 3
    assert await cache.get(1, 0) is None  # oldest evicted
    assert await cache.get(1, 4) is not None


async def test_pop_by_message_id_resolves_unknown_chat():
    # Telethon's MessageDeleted has chat_id=None for DMs/small groups — this
    # is the fallback path that makes deletion capture work there anyway.
    cache = _cache()
    await cache.remember(chat_id=555, message_id=42, sender_id=7, text="secret")
    found = await cache.pop_by_message_id(42)
    assert found is not None
    assert found.chat_id == 555 and found.text == "secret"
    # popped once -> gone from both indexes
    assert await cache.pop_by_message_id(42) is None
    assert await cache.get(555, 42) is None


async def test_pop_by_message_id_prefers_most_recent_on_collision():
    cache = _cache()
    await cache.remember(chat_id=1, message_id=99, sender_id=1, text="first chat")
    await cache.remember(chat_id=2, message_id=99, sender_id=2, text="second chat")  # same id, different chat
    found = await cache.pop_by_message_id(99)
    assert found.chat_id == 2 and found.text == "second chat"


async def test_pop_by_message_id_unknown_returns_none():
    cache = _cache()
    assert await cache.pop_by_message_id(12345) is None


async def test_cache_remembers_media_alongside_empty_text():
    # A caption-less photo/voice message has raw_text == "" — this is exactly
    # what let deleted photos/voice slip past anti-delete silently before
    # (autosave.py's on_deleted skipped anything with falsy cached.text,
    # media or not); the cache itself must still hold the bytes regardless.
    cache = _cache()
    await cache.remember(chat_id=10, message_id=1, sender_id=5, text="", media=b"\x89PNG...", media_kind="photo")
    got = await cache.get(10, 1)
    assert got is not None
    assert got.text == ""
    assert got.media == b"\x89PNG..."
    assert got.media_kind == "photo"

    popped = await cache.pop(10, 1)
    assert popped.media == b"\x89PNG..." and popped.media_kind == "photo"


async def test_cache_remembers_location():
    cache = _cache()
    await cache.remember(chat_id=10, message_id=1, sender_id=5, text="", location=(48.5, 31.1))
    got = await cache.get(10, 1)
    assert got is not None and got.location == (48.5, 31.1)


async def test_cache_connect_race_opens_only_one_connection(monkeypatch):
    # A burst of concurrent remember() calls right after startup (before the
    # cache has ever connected) must not each open their own SQLite
    # connection — only the first should win, the rest reuse it.
    real_connect = aiosqlite.connect
    call_count = 0

    def counting_connect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(aiosqlite, "connect", counting_connect)
    cache = _cache()
    await asyncio.gather(*(
        cache.remember(chat_id=1, message_id=i, sender_id=None, text=str(i)) for i in range(20)
    ))
    assert call_count == 1
    assert await cache.count() == 20


async def test_cache_scoped_per_owner():
    # two owners sharing the same file must never see each other's rows
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        owner1 = RecentMessageCache(owner_user_id=1, db_path=path)
        owner2 = RecentMessageCache(owner_user_id=2, db_path=path)
        await owner1.remember(chat_id=10, message_id=1, sender_id=5, text="owner1's message")
        assert await owner2.get(10, 1) is None
        assert (await owner1.get(10, 1)).text == "owner1's message"
        await owner1.close()
        await owner2.close()
    finally:
        os.remove(path)
