"""Crypto Pay provider (mocked HTTP) + pending/activate subscription helpers."""

from datetime import datetime

import httpx
import pytest

from db.models import SubscriptionStatus
from management_bot import subscriptions
from management_bot.payment.crypto_pay import CryptoPayError, CryptoPayProvider
from shared.tariffs import Tariff

NOW = datetime(2026, 8, 22)


def _provider(handler) -> CryptoPayProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CryptoPayProvider("test-token", client=client)


async def test_crypto_create_invoice():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/createInvoice")
        assert request.headers["Crypto-Pay-API-Token"] == "test-token"
        return httpx.Response(200, json={"ok": True, "result": {"invoice_id": 123, "bot_invoice_url": "https://t.me/pay"}})

    inv = await _provider(handler).create_invoice(amount=99, description="Sub", payload="sub:1")
    assert inv.invoice_id == "123"
    assert inv.pay_url == "https://t.me/pay"


async def test_crypto_is_paid():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getInvoices")
        return httpx.Response(200, json={"ok": True, "result": {"items": [{"status": "paid"}]}})

    assert await _provider(handler).is_paid("123") is True


async def test_crypto_not_paid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"items": [{"status": "active"}]}})

    assert await _provider(handler).is_paid("123") is False


async def test_crypto_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": {"code": 401}})

    with pytest.raises(CryptoPayError):
        await _provider(handler).create_invoice(amount=1, description="x", payload="p")


async def test_create_pending_and_activate(db_session):
    sub = await subscriptions.create_pending(
        db_session, telegram_id=1, tariff=Tariff.PRO, provider_name="stars"
    )
    assert sub.status == SubscriptionStatus.PENDING
    assert sub.payment_provider == "stars"

    got = await subscriptions.get_subscription(db_session, sub.id)
    assert got is not None and got.id == sub.id

    await subscriptions.activate(db_session, sub, now=NOW)
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.started_at == NOW
    assert sub.started_at.tzinfo is None
    assert sub.expires_at > sub.started_at


async def test_activate_uses_period_days_from_the_row(db_session):
    # period_days is chosen at create_pending time (the duration the buyer
    # picked) and must round-trip through activate() without being passed
    # again explicitly — a Stars invoice payload has no room for it.
    sub = await subscriptions.create_pending(
        db_session, telegram_id=2, tariff=Tariff.STANDARD, provider_name="stars", period_days=90
    )
    await subscriptions.activate(db_session, sub, now=NOW)
    assert (sub.expires_at - sub.started_at).days == 90


# --- the duration grid -----------------------------------------------------


async def _grid():
    from management_bot.handlers.subscribe import _duration_kb
    from shared.pricing import compute_prices
    from shared.tariffs import DURATIONS, effective_profit_uah, get_plan

    plan = get_plan("pro")
    prices = {
        code: await compute_prices(effective_profit_uah(plan.profit_uah, opt, 0), usd_uah_rate=44.74)
        for code, opt in DURATIONS.items()
    }
    return _duration_kb("pro", 0, "uk", prices), prices


async def test_hryvnia_is_the_first_price_row_and_charges_a_card():
    """It used to be an inert reference figure below the other channels."""
    kb, prices = await _grid()
    label_row, first_price_row = kb.inline_keyboard[0], kb.inline_keyboard[1]

    assert all("noop" in b.callback_data for b in label_row), "top row is the duration labels"
    assert all(b.text.endswith("₴") for b in first_price_row)
    assert all(b.callback_data.endswith(":card") for b in first_price_row)


async def test_the_grid_quotes_round_hryvnia_prices():
    """The card price is shown exactly as the tariff is priced — 500₴, not a
    grossed-up 511₴. Chosen for legibility; the acquirer fee comes out of the
    margin instead."""
    kb, prices = await _grid()
    shown = {b.text for b in kb.inline_keyboard[1]}
    assert shown == {f"{p.uah_invoice}₴" for p in prices.values()}
    assert shown == {f"{p.profit_uah}₴" for p in prices.values()}


async def test_stars_and_crypto_keep_their_own_rows():
    kb, _ = await _grid()
    stars_row, usd_row = kb.inline_keyboard[2], kb.inline_keyboard[3]
    assert all(b.callback_data.endswith(":stars") for b in stars_row)
    assert all(b.callback_data.endswith(":crypto") for b in usd_row)


# --- the "connect your account" nudge --------------------------------------


async def test_success_message_skips_the_connect_nudge_when_already_connected(db_session):
    """It used to be appended unconditionally, pointing someone with a live
    account at a settings screen whose button says the opposite."""
    from management_bot import storage
    from management_bot.handlers.subscribe import _success_text
    from shared.i18n import t

    await storage.upsert_user(db_session, 3001)
    await storage.save_session(
        db_session, telegram_id=3001, phone_number="+380000000000", session_string="s"
    )
    await db_session.commit()

    text = await _success_text(db_session, 3001, "uk", "Pro")
    assert t("uk", "sub_connect_hint") not in text
    assert "Pro" in text


async def test_success_message_keeps_the_nudge_for_someone_with_no_account(db_session):
    from management_bot import storage
    from management_bot.handlers.subscribe import _success_text
    from shared.i18n import t

    await storage.upsert_user(db_session, 3002)
    await db_session.commit()

    text = await _success_text(db_session, 3002, "uk", "Pro")
    assert t("uk", "sub_connect_hint") in text
