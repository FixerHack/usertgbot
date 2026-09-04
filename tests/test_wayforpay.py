"""WayForPay protocol details — signatures, amount formatting, regular payments.

Every assertion about a signature here recomputes it independently from the
field order in the documentation (wiki.wayforpay.com/view/852102) rather than
calling the module's own helper on both sides, which would only prove the code
agrees with itself. A wrong field order is invisible in testing and fatal in
production: the gateway simply rejects everything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date

import pytest

from management_bot.payment.wayforpay import (
    TEST_MERCHANT_ACCOUNT,
    TEST_MERCHANT_SECRET,
    RegularSpec,
    WayForPayError,
    WayForPayProvider,
    parse_callback_body,
)

DOMAIN = "moozuku.tech"


def _md5(secret: str, *values: str) -> str:
    return hmac.new(secret.encode(), ";".join(values).encode(), hashlib.md5).hexdigest()


def _provider(client=None) -> WayForPayProvider:
    return WayForPayProvider(TEST_MERCHANT_ACCOUNT, TEST_MERCHANT_SECRET, DOMAIN, client=client)


# --- purchase --------------------------------------------------------------


def test_purchase_signature_follows_the_documented_field_order():
    form = _provider().build_purchase(
        order_reference="sub-7-1700000000",
        amount=515,
        product_name="Pro 30d",
        return_url="https://moozuku.tech/pay/done",
        service_url="https://moozuku.tech/pay/wayforpay/callback",
        order_date=1700000000,
    )
    expected = _md5(
        TEST_MERCHANT_SECRET,
        TEST_MERCHANT_ACCOUNT, DOMAIN, "sub-7-1700000000", "1700000000",
        "515", "UAH", "Pro 30d", "1", "515",
    )
    assert form.fields["merchantSignature"] == expected
    assert form.action == "https://secure.wayforpay.com/pay"


def test_signed_amount_is_the_amount_actually_submitted():
    """The signature covers the amount as text. Signing "515" while posting
    "515.0" (or vice versa) is rejected by the gateway with no useful error."""
    form = _provider().build_purchase(
        order_reference="sub-1-1", amount=500, product_name="p",
        return_url="r", service_url="s", order_date=1,
    )
    assert form.fields["amount"] == "500"
    assert form.fields["productPrice[]"] == ["500"]
    assert form.fields["merchantSignature"] == _md5(
        TEST_MERCHANT_SECRET, TEST_MERCHANT_ACCOUNT, DOMAIN, "sub-1-1", "1", "500", "UAH", "p", "1", "500"
    )


def test_regular_payment_is_preset_so_the_client_cannot_rewrite_it():
    form = _provider().build_purchase(
        order_reference="sub-1-1", amount=515, product_name="p",
        return_url="r", service_url="s", order_date=1,
        regular=RegularSpec(mode="monthly", amount=515, date_next=date(2026, 10, 4)),
    )
    assert form.fields["regularMode"] == "monthly"
    assert form.fields["regularOn"] == "1"
    # Without "preset" the payment page lets the buyer change the schedule —
    # a yearly price could be set to renew daily.
    assert form.fields["regularBehavior"] == "preset"
    assert form.fields["dateNext"] == "04.10.2026"
    assert form.fields["regularAmount"] == "515"


def test_no_regular_fields_on_a_one_off_purchase():
    form = _provider().build_purchase(
        order_reference="sub-1-1", amount=515, product_name="p",
        return_url="r", service_url="s", order_date=1,
    )
    assert not any(key.startswith("regular") for key in form.fields)
    assert "dateNext" not in form.fields


# --- callback --------------------------------------------------------------


def _callback_payload(**over) -> dict:
    payload = {
        "merchantAccount": TEST_MERCHANT_ACCOUNT,
        "orderReference": "sub-7-1700000000",
        "amount": "515",
        "currency": "UAH",
        "authCode": "123456",
        "cardPan": "44**44",
        "transactionStatus": "Approved",
        "reasonCode": "1100",
        "processingDate": "1700000100",
    }
    payload.update(over)
    payload["merchantSignature"] = _md5(
        TEST_MERCHANT_SECRET,
        payload["merchantAccount"], payload["orderReference"], payload["amount"],
        payload["currency"], payload["authCode"], payload["cardPan"],
        payload["transactionStatus"], payload["reasonCode"],
    )
    return payload


def test_valid_callback_verifies():
    assert _provider().verify_callback(_callback_payload()) is True


def test_tampered_amount_fails_verification():
    payload = _callback_payload()
    payload["amount"] = "1"
    assert _provider().verify_callback(payload) is False


def test_callback_for_another_merchant_is_rejected():
    """Same secret, different shop: the signature would still match, so the
    merchant name has to be checked separately."""
    payload = _callback_payload(merchantAccount="someone_else")
    assert _provider().verify_callback(payload) is False


def test_numbers_keep_their_wire_representation():
    """The gateway signs the characters it sent. A JSON `515` round-tripped
    through float becomes "515.0", which signs differently — so the parser
    keeps the original text."""
    payload = _callback_payload()
    body = json.dumps({**payload, "amount": 515})
    parsed = parse_callback_body(body)

    assert parsed["amount"] == "515"
    assert _provider().verify_callback(parsed) is True

    # And the naive parse really does break it — this is what the hook avoids.
    naive = json.loads(body)
    assert str(naive["amount"]) == "515"  # int survives; a float would not
    float_body = body.replace('"amount": 515', '"amount": 515.00')
    assert str(json.loads(float_body)["amount"]) == "515.0"
    assert parse_callback_body(float_body)["amount"] == "515.00"


def test_form_encoded_callback_body_is_tolerated():
    """Observed in the wild: the JSON arrives as a form key with an empty
    value, leaving a trailing '=' that plain json.loads chokes on."""
    payload = _callback_payload()
    parsed = parse_callback_body(json.dumps(payload) + "=")
    assert parsed["orderReference"] == payload["orderReference"]


def test_read_callback_flags_a_decline():
    result = _provider().read_callback(_callback_payload(transactionStatus="Declined", reasonCode="1104"))
    assert result.approved is False
    assert result.reason_code == "1104"


def test_callback_response_is_signed():
    response = _provider().callback_response("sub-7-1", now=1700000200)
    assert response["status"] == "accept"
    assert response["signature"] == _md5(TEST_MERCHANT_SECRET, "sub-7-1", "accept", "1700000200")


# --- regular payment management -------------------------------------------


class _FakeClient:
    def __init__(self, response: dict):
        self._response = response
        self.sent: dict | None = None

    async def post(self, url, json):  # noqa: A002 - httpx's own parameter name
        self.sent = {"url": url, "json": json}

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        return _Resp(self._response)


async def test_remove_regular_sends_credentials_and_accepts_4100():
    client = _FakeClient({"REASONCODE": 4100, "REASON": "OK", "ORDERREFERENCE": "sub-7-1"})
    await _provider(client).remove_regular("sub-7-1")

    assert client.sent["url"] == "https://api.wayforpay.com/regularApi"
    assert client.sent["json"]["requestType"] == "REMOVE"
    assert client.sent["json"]["merchantPassword"] == TEST_MERCHANT_SECRET
    assert client.sent["json"]["orderReference"] == "sub-7-1"


async def test_regular_api_uses_the_merchant_password_not_the_secret_key():
    """The cabinet issues both, and they are different values. Sending the key
    here fails only the cancel call — payments keep working, so the breakage
    shows up as "cancel does nothing" long after deploy."""
    client = _FakeClient({"REASONCODE": 4100, "REASON": "OK"})
    provider = WayForPayProvider(
        TEST_MERCHANT_ACCOUNT, TEST_MERCHANT_SECRET, DOMAIN,
        merchant_password="separate-password", client=client,
    )
    await provider.remove_regular("sub-7-1")
    assert client.sent["json"]["merchantPassword"] == "separate-password"


async def test_regular_failure_raises_rather_than_reporting_success():
    """A cancel that quietly failed would tell the user their card is safe
    while it keeps being charged."""
    client = _FakeClient({"REASONCODE": 4103, "REASON": "Order not found"})
    with pytest.raises(WayForPayError):
        await _provider(client).remove_regular("sub-7-1")
