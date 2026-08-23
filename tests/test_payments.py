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
