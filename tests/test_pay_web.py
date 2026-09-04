"""pay_web — the payment page and, more importantly, the callback.

The callback is the only thing that turns money into an active subscription,
and WayForPay re-delivers it until it gets a signed "accept". So the cases
that matter are the unhappy ones: a forged body, a re-delivered charge, a sum
that doesn't match what we asked for, and a renewal that the bank declined.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta

from db.models import SubscriptionStatus
from management_bot import subscriptions
from management_bot.payment.wayforpay import (
    TEST_MERCHANT_ACCOUNT,
    TEST_MERCHANT_SECRET,
    WayForPayProvider,
)
from shared.tariffs import Tariff

DOMAIN = "moozuku.tech"
PROVIDER = WayForPayProvider(TEST_MERCHANT_ACCOUNT, TEST_MERCHANT_SECRET, DOMAIN)


class _Notifier:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return True


async def _client(db_session, *, provider=PROVIDER, notifier=None, bot_username=None):
    from httpx import ASGITransport, AsyncClient

    from pay_web.app import create_app

    async def _db():
        yield db_session

    app = create_app(
        provider=provider,
        base_url=lambda: "https://moozuku.tech",
        db_dependency=_db,
        notifier=notifier,
        bot_username=bot_username,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _pending(db_session, *, amount=515, days=30, auto_renew=True, telegram_id=555):
    sub = await subscriptions.create_pending(
        db_session,
        telegram_id=telegram_id,
        tariff=Tariff.PRO,
        provider_name="wayforpay",
        period_days=days,
        auto_renew=auto_renew,
        amount_uah=amount,
    )
    sub.order_reference = subscriptions.build_order_reference(sub.id, now=1700000000)
    await db_session.commit()
    return sub


def _signed_callback(order_reference: str, *, amount="515", status="Approved", auth_code="123456",
                     processing_date="1700000100") -> str:
    payload = {
        "merchantAccount": TEST_MERCHANT_ACCOUNT,
        "orderReference": order_reference,
        "amount": amount,
        "currency": "UAH",
        "authCode": auth_code,
        "cardPan": "44**44",
        "transactionStatus": status,
        "reasonCode": "1100" if status == "Approved" else "1104",
        "processingDate": processing_date,
    }
    payload["merchantSignature"] = hmac.new(
        TEST_MERCHANT_SECRET.encode(),
        ";".join(
            [
                payload["merchantAccount"], payload["orderReference"], payload["amount"],
                payload["currency"], payload["authCode"], payload["cardPan"],
                payload["transactionStatus"], payload["reasonCode"],
            ]
        ).encode(),
        hashlib.md5,
    ).hexdigest()
    return json.dumps(payload)


# --- the page --------------------------------------------------------------


async def test_page_renders_the_signed_form(db_session):
    sub = await _pending(db_session)
    async with await _client(db_session) as ac:
        r = await ac.get(f"/{sub.order_reference}")

    assert r.status_code == 200
    assert 'action="https://secure.wayforpay.com/pay"' in r.text
    assert 'name="merchantSignature"' in r.text
    assert 'name="regularMode" value="monthly"' in r.text
    assert "515 ₴" in r.text


async def test_unknown_order_reference_is_a_page_not_a_stack_trace(db_session):
    async with await _client(db_session) as ac:
        r = await ac.get("/sub-999-1")
    assert r.status_code == 404
    assert "<h1>" in r.text


async def test_paid_subscription_does_not_offer_to_pay_again(db_session):
    sub = await _pending(db_session)
    await subscriptions.activate(db_session, sub)
    await db_session.commit()

    async with await _client(db_session) as ac:
        r = await ac.get(f"/{sub.order_reference}")
    assert "secure.wayforpay.com" not in r.text


async def test_a_quarterly_plan_asks_for_the_quarterly_schedule(db_session):
    sub = await _pending(db_session, days=90)
    async with await _client(db_session) as ac:
        r = await ac.get(f"/{sub.order_reference}")
    assert 'name="regularMode" value="quarterly"' in r.text


# --- the callback ----------------------------------------------------------


async def test_approved_callback_activates_the_subscription(db_session):
    sub = await _pending(db_session)
    notifier = _Notifier()

    async with await _client(db_session, notifier=notifier) as ac:
        r = await ac.post("/wayforpay/callback", content=_signed_callback(sub.order_reference))

    assert r.status_code == 200
    assert r.json()["status"] == "accept"

    await db_session.refresh(sub)
    assert sub.status is SubscriptionStatus.ACTIVE
    assert sub.expires_at is not None
    assert notifier.sent and notifier.sent[0][0] == 555


async def test_forged_callback_changes_nothing_and_is_not_acknowledged(db_session):
    sub = await _pending(db_session)
    body = json.loads(_signed_callback(sub.order_reference))
    body["merchantSignature"] = "0" * 32

    async with await _client(db_session) as ac:
        r = await ac.post("/wayforpay/callback", content=json.dumps(body))

    # Not an "accept": acknowledging an unauthenticated body would stop the
    # real callback from ever being retried.
    assert r.status_code == 400
    await db_session.refresh(sub)
    assert sub.status is SubscriptionStatus.PENDING


async def test_the_same_charge_delivered_twice_is_applied_once(db_session):
    sub = await _pending(db_session)
    body = _signed_callback(sub.order_reference)

    async with await _client(db_session) as ac:
        first = await ac.post("/wayforpay/callback", content=body)
        await db_session.refresh(sub)
        expires_after_first = sub.expires_at
        second = await ac.post("/wayforpay/callback", content=body)

    await db_session.refresh(sub)
    assert first.json()["status"] == "accept"
    # Still acknowledged, or the gateway keeps redelivering forever.
    assert second.json()["status"] == "accept"
    assert sub.expires_at == expires_after_first


async def test_amount_mismatch_does_not_grant_the_subscription(db_session):
    """Signed, but not for the sum we asked. Either the gateway changed
    something or we have a bug — neither should hand out a tariff."""
    sub = await _pending(db_session, amount=515)

    async with await _client(db_session) as ac:
        r = await ac.post("/wayforpay/callback", content=_signed_callback(sub.order_reference, amount="1"))

    assert r.json()["status"] == "accept"
    await db_session.refresh(sub)
    assert sub.status is SubscriptionStatus.PENDING


async def test_renewal_extends_from_the_current_expiry(db_session):
    sub = await _pending(db_session)
    await subscriptions.activate(db_session, sub)
    await db_session.commit()
    before = sub.expires_at

    async with await _client(db_session) as ac:
        r = await ac.post(
            "/wayforpay/callback",
            content=_signed_callback(sub.order_reference, auth_code="999", processing_date="1700999999"),
        )

    assert r.json()["status"] == "accept"
    await db_session.refresh(sub)
    # A renewal that lands early must not shorten the period already paid for.
    assert sub.expires_at == before + timedelta(days=30)


async def test_declined_renewal_tells_the_owner(db_session):
    sub = await _pending(db_session)
    await subscriptions.activate(db_session, sub)
    await db_session.commit()
    expires = sub.expires_at
    notifier = _Notifier()

    async with await _client(db_session, notifier=notifier) as ac:
        await ac.post(
            "/wayforpay/callback",
            content=_signed_callback(sub.order_reference, status="Declined", auth_code="x", processing_date="1701"),
        )

    await db_session.refresh(sub)
    assert sub.expires_at == expires
    assert notifier.sent, "a failed renewal has to reach the owner — the tariff stops otherwise"


async def test_renewal_reported_under_a_suffixed_reference_still_lands(db_session):
    """The reference a regular charge is reported under is not guaranteed to
    be the original one, so the subscription id is embedded in it."""
    sub = await _pending(db_session)
    await subscriptions.activate(db_session, sub)
    await db_session.commit()
    before = sub.expires_at

    async with await _client(db_session) as ac:
        await ac.post("/wayforpay/callback", content=_signed_callback(f"{sub.order_reference}_1"))

    await db_session.refresh(sub)
    assert sub.expires_at == before + timedelta(days=30)


async def test_callback_for_an_unknown_order_is_acknowledged_not_retried(db_session):
    async with await _client(db_session) as ac:
        r = await ac.post("/wayforpay/callback", content=_signed_callback("sub-4242-1"))
    assert r.json()["status"] == "accept"


async def test_gateway_not_configured_is_not_a_500(db_session):
    async with await _client(db_session, provider=None) as ac:
        page = await ac.get("/sub-1-1")
        callback = await ac.post("/wayforpay/callback", content="{}")
    assert page.status_code == 404
    assert callback.status_code == 503


# --- amount/period arithmetic ---------------------------------------------


async def test_expiry_is_the_period_the_buyer_paid_for(db_session):
    sub = await _pending(db_session, days=365)
    async with await _client(db_session) as ac:
        await ac.post("/wayforpay/callback", content=_signed_callback(sub.order_reference))

    await db_session.refresh(sub)
    assert (sub.expires_at - datetime.utcnow()).days >= 364


async def test_return_page_accepts_the_gateways_post(db_session):
    """WayForPay POSTs the result to returnUrl unless that is switched off in
    the cabinet — a GET-only route would show the buyer a 405 right after
    they paid."""
    async with await _client(db_session) as ac:
        get = await ac.get("/done?lang=uk")
        post = await ac.post("/done?lang=uk", data={"transactionStatus": "Approved"})

    assert get.status_code == 200
    assert post.status_code == 200
    assert "<h1>" in post.text


async def test_mounted_under_connect_the_pay_app_keeps_its_own_csp(db_session):
    """The two apps share an origin but not a policy: the payment page must be
    allowed to POST a form to the gateway, which connect_web's CSP forbids.
    Parent middleware runs for mounted routes too, so this seam is exactly
    where the payment page would silently stop submitting."""
    from httpx import ASGITransport, AsyncClient

    from connect_web.app import create_app as create_connect_app
    from pay_web.app import create_app as create_pay_app

    async def _db():
        yield db_session

    sub = await _pending(db_session)

    root = create_connect_app(
        bot_token="8123456:AAF-test-token-value", webapp_api_id=1, webapp_api_hash="h",
        db_dependency=_db,
    )
    root.mount("/pay", create_pay_app(provider=PROVIDER, base_url=lambda: "https://x", db_dependency=_db))

    async with AsyncClient(transport=ASGITransport(app=root), base_url="http://t") as ac:
        pay = await ac.get(f"/pay/{sub.order_reference}")
        connect = await ac.get("/connect/nope")

    assert "form-action https://secure.wayforpay.com" in pay.headers["content-security-policy"]
    assert "form-action 'none'" in connect.headers["content-security-policy"]


async def test_payment_page_submits_itself(db_session):
    """The gateway takes a POST and the invoice API that would give us a plain
    link cannot carry a recurring schedule, so this page is unavoidable — but
    a tap on it is not."""
    sub = await _pending(db_session)
    async with await _client(db_session) as ac:
        page = await ac.get(f"/{sub.order_reference}")
        script = await ac.get("/pay.js")

    assert 'id="pay-form"' in page.text
    assert '<script src="/pay/pay.js">' in page.text
    assert script.status_code == 200
    # The visible button stays: it is what happens when scripting is off.
    assert 'type="submit"' in page.text


async def test_terminal_pages_carry_no_auto_submit(db_session):
    """An expired page has no form; loading the submitter there would throw
    in the console of a page a confused user is already looking at."""
    async with await _client(db_session) as ac:
        expired = await ac.get("/sub-999-1")
    assert "pay.js" not in expired.text


async def test_payment_pages_may_not_be_framed(db_session):
    """These open in a real browser, never embedded. Allowing a frame would
    let another page wrap the payment step in its own chrome."""
    sub = await _pending(db_session)
    async with await _client(db_session) as ac:
        page = await ac.get(f"/{sub.order_reference}")

    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "form-action https://secure.wayforpay.com" in csp
    # The gateway is the ONLY thing this policy opens up.
    assert "script-src 'self';" in csp
    assert "telegram" not in csp


async def test_done_page_links_back_to_the_bot(db_session):
    async with await _client(db_session, bot_username="useagentperbot") as ac:
        done = await ac.get("/done?lang=uk")
    assert "https://t.me/useagentperbot" in done.text
