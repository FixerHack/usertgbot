"""Admin panel data layer + API endpoints."""

from datetime import date, datetime

from admin_web import service
from db.models import Subscription, SubscriptionStatus
from management_bot.storage import save_session, upsert_user

NOW = datetime(2026, 8, 22)


async def _seed(db):
    # user 1: pro + connected
    u1 = await upsert_user(db, 1001, username="alice", full_name="Alice")
    db.add(Subscription(user_id=u1.id, tariff="pro", status=SubscriptionStatus.ACTIVE, payment_provider="stub"))
    await save_session(db, telegram_id=1001, phone_number="+111", session_string="s")
    # user 2: no sub, blocked
    u2 = await upsert_user(db, 1002, username="bob")
    u2.is_blocked = True
    await db.flush()
    return u1, u2


async def test_metrics(db_session):
    await _seed(db_session)
    m = await service.get_metrics(db_session, now=NOW)
    assert m.total_users == 2
    assert m.blocked_users == 1
    assert m.connected_accounts == 1
    assert m.active_subscriptions == 1
    assert m.by_tariff["pro"] == 1
    assert m.by_tariff["standard"] == 0


async def test_list_users_and_filters(db_session):
    await _seed(db_session)

    all_rows = await service.list_users(db_session, now=NOW)
    assert {r.telegram_id for r in all_rows} == {1001, 1002}

    pro = await service.list_users(db_session, tariff="pro", now=NOW)
    assert [r.telegram_id for r in pro] == [1001]

    connected = await service.list_users(db_session, connected=True, now=NOW)
    assert [r.telegram_id for r in connected] == [1001]

    blocked = await service.list_users(db_session, blocked=True, now=NOW)
    assert [r.telegram_id for r in blocked] == [1002]


async def test_grant_revoke_block(db_session):
    await upsert_user(db_session, 2001, username="carol")

    sub = await service.grant_subscription(db_session, 2001, "standard", days=30, now=NOW)
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.expires_at > NOW

    rows = await service.list_users(db_session, tariff="standard", now=NOW)
    assert [r.telegram_id for r in rows] == [2001]

    revoked = await service.revoke_subscription(db_session, 2001, now=NOW)
    assert revoked == 1
    assert await service.list_users(db_session, tariff="standard", now=NOW) == []

    assert await service.set_blocked(db_session, 2001, True) is True
    row = (await service.list_users(db_session, now=NOW))[0]
    assert row.is_blocked is True


async def test_delete_user(db_session):
    # a user WITH children (subscription + connected session) — this is
    # exactly what caught a real bug: SQLAlchemy's default relationship
    # cascade tries to NULL out the NOT NULL user_id on children before
    # deleting the parent, unless the relationship is passive_deletes=True
    # (letting the DB's own ON DELETE CASCADE handle it instead)
    await upsert_user(db_session, 3001, username="dave")
    await service.grant_subscription(db_session, 3001, "pro", days=30, now=NOW)
    await save_session(db_session, telegram_id=3001, phone_number="+222", session_string="s")
    await upsert_user(db_session, 3002, username="erin")

    assert await service.delete_user(db_session, 3001) is True
    assert [r.telegram_id for r in await service.list_users(db_session, now=NOW)] == [3002]

    # deleting again (already gone) reports False, doesn't error
    assert await service.delete_user(db_session, 3001) is False
    # deleting a telegram_id that never existed also reports False
    assert await service.delete_user(db_session, 999999) is False


async def test_activity_series(db_session):
    from db.queries import save_captured_message

    u1 = await upsert_user(db_session, 1001)
    today = date(2026, 8, 22)
    yesterday = date(2026, 8, 21)

    # 2 events today, 1 yesterday (saved_at is server_default=now(), so we
    # patch it directly after insert to control the date deterministically)
    for _ in range(2):
        row = await save_captured_message(
            db_session, owner_user_id=u1.id, chat_id=1, message_id=1, event_type="deleted", text="x"
        )
        row.saved_at = datetime.combine(today, datetime.min.time())
    row = await save_captured_message(
        db_session, owner_user_id=u1.id, chat_id=1, message_id=2, event_type="deleted", text="y"
    )
    row.saved_at = datetime.combine(yesterday, datetime.min.time())
    await db_session.flush()

    series = await service.get_activity_series(db_session, days=3, end=today)
    by_date = {p["date"]: p["count"] for p in series}
    assert by_date[today.isoformat()] == 2
    assert by_date[yesterday.isoformat()] == 1
    assert len(series) == 3


async def test_top_users_today(db_session):
    from db.queries import save_captured_message

    u1 = await upsert_user(db_session, 1001, username="alice")
    u2 = await upsert_user(db_session, 1002, username="bob")
    today = date(2026, 8, 22)

    for owner, n in ((u1, 3), (u2, 1)):
        for i in range(n):
            row = await save_captured_message(
                db_session, owner_user_id=owner.id, chat_id=1, message_id=i, event_type="deleted", text="x"
            )
            row.saved_at = datetime.combine(today, datetime.min.time())
    await db_session.flush()

    top = await service.get_top_users_today(db_session, day=today)
    assert top[0]["username"] == "alice" and top[0]["count"] == 3
    assert top[1]["username"] == "bob" and top[1]["count"] == 1


async def test_metrics_includes_activity_and_top_users(db_session):
    await _seed(db_session)
    m = await service.get_metrics(db_session, now=NOW)
    assert len(m.activity_7d) == 7
    assert isinstance(m.top_users_today, list)


async def test_grant_unknown_tariff(db_session):
    import pytest

    with pytest.raises(ValueError):
        await service.grant_subscription(db_session, 2001, "gold")


async def test_api_endpoints(db_session):
    from httpx import ASGITransport, AsyncClient

    from admin_web.app import create_app

    await _seed(db_session)

    async def _db():
        yield db_session

    app = create_app("secret", db_dependency=_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        assert (await ac.get("/api/metrics")).status_code == 401  # no token
        r = await ac.get("/api/metrics", headers={"X-Token": "secret"})
        assert r.status_code == 200 and r.json()["total_users"] == 2

        users = (await ac.get("/api/users?tariff=pro", headers={"X-Token": "secret"})).json()
        assert [u["telegram_id"] for u in users] == [1001]

        # grant via API
        g = await ac.post("/api/users/1002/grant?tariff=standard&days=15", headers={"X-Token": "secret"})
        assert g.status_code == 200
        std = (await ac.get("/api/users?tariff=standard", headers={"X-Token": "secret"})).json()
        assert [u["telegram_id"] for u in std] == [1002]

        # index page served without token
        index = await ac.get("/")
        assert index.status_code == 200 and "Admin" in index.text
