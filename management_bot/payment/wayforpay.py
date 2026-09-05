"""WayForPay (card acquiring, UAH) — purchase form, callback, regular payments.

Docs: https://wiki.wayforpay.com/view/852102 (Purchase),
      https://wiki.wayforpay.com/view/852496 (regular payments).

Two things make this gateway different from the other two channels here:

1. It is NOT a link. Crypto Pay hands back a `pay_url` that goes straight into
   an inline button; WayForPay expects a browser to POST a signed form to
   https://secure.wayforpay.com/pay. So the bot's button points at our own
   page, which auto-submits that form (see pay_web/).

2. Renewals are the gateway's job, not ours. Passing `regularMode` on the
   first purchase makes WayForPay charge the card on schedule, warn the client
   before each charge and retry a failed one the next day — all of which we
   would otherwise have to build and get right ourselves. We only listen to
   the callbacks and extend the subscription.

Every signature here is HMAC-MD5 over ";"-joined values with the merchant
secret key. MD5 is not our choice — it is what the gateway specifies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date

import httpx

logger = logging.getLogger(__name__)

PURCHASE_URL = "https://secure.wayforpay.com/pay"
REGULAR_API_URL = "https://api.wayforpay.com/regularApi"

# Sandbox merchant from the docs (wiki.wayforpay.com/view/852472) — lets the
# whole flow be exercised end to end before a real merchant is approved.
TEST_MERCHANT_ACCOUNT = "test_merch_n1"
TEST_MERCHANT_SECRET = "flk3409refn54t54t*FNJRET"

# Fields the callback signature covers, in the exact documented order. The
# order IS the protocol: a single field out of place produces a signature that
# is wrong in a way nothing reports except every payment silently failing.
_CALLBACK_SIGNATURE_FIELDS = (
    "merchantAccount", "orderReference", "amount", "currency",
    "authCode", "cardPan", "transactionStatus", "reasonCode",
)

APPROVED = "Approved"
# The only status that means a bank actually refused a charge, and therefore
# the only one WayForPay will retry. "Expired" (the payer walked away),
# "Refunded" and "Voided" are not failures to retry, and telling someone their
# bank declined when their money was refunded is worse than saying nothing.
DECLINED = "Declined"

# regularApi answers with a numeric code; these two both mean "accepted".
_REGULAR_OK_CODES = {"1100", "4100"}


class WayForPayError(Exception):
    pass


@dataclass(frozen=True)
class RegularSpec:
    """Schedule for gateway-driven renewals."""

    mode: str          # monthly | quarterly | yearly (WayForPay vocabulary)
    amount: int        # charged each period; may differ from the first charge
    date_next: date    # first renewal, DD.MM.YYYY on the wire


@dataclass(frozen=True)
class PurchaseForm:
    """Everything an HTML form needs to POST the user to the gateway.

    `fields` values are str or list[str] — the product arrays are genuinely
    repeated inputs (productName[]), not comma-joined.
    """

    action: str
    fields: dict[str, str | list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class CallbackResult:
    order_reference: str
    approved: bool
    amount: float
    currency: str
    transaction_status: str
    reason: str
    reason_code: str
    processing_date: str
    auth_code: str
    rec_token: str | None
    raw: dict[str, object]


def _fmt_amount(value: float | int) -> str:
    """One representation, used for BOTH the signed string and the submitted
    field. Whole hryvnia stays "515", not "515.0" — signing one form and
    sending the other is the classic way this integration breaks."""
    return str(int(value)) if float(value).is_integer() else f"{float(value):.2f}"


def sign(secret_key: str, values: list[str]) -> str:
    return hmac.new(secret_key.encode(), ";".join(values).encode(), hashlib.md5).hexdigest()


def parse_callback_body(body: bytes | str) -> dict[str, object]:
    """Parse the callback keeping numbers as their ORIGINAL text.

    The gateway signs the literal characters it sent. Round-tripping `515`
    through a Python float and back yields "515.0", which signs differently —
    so numbers are kept as the exact substrings that arrived.
    """
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    # The callback has been observed to arrive as a form body whose whole key
    # is the JSON and whose value is empty; tolerate that rather than 400.
    text = text.strip()
    if text.endswith("="):
        text = text[:-1]
    return json.loads(text, parse_float=str, parse_int=str)


class WayForPayProvider:
    name = "wayforpay"

    def __init__(
        self,
        merchant_account: str,
        secret_key: str,
        domain: str,
        *,
        merchant_password: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._account = merchant_account
        self._secret = secret_key
        self._domain = domain
        # The cabinet lists "Merchant secret key" and "Merchant password" as
        # two SEPARATE values: signatures use the key, regularApi authenticates
        # with the password. Falls back to the key only so a half-configured
        # deployment fails at the API with a clear error rather than at import.
        self._password = merchant_password or secret_key
        self._client = client  # tests inject a mocked client

    # --- purchase ----------------------------------------------------------

    def build_purchase(
        self,
        *,
        order_reference: str,
        amount: int,
        product_name: str,
        return_url: str,
        service_url: str,
        order_date: int | None = None,
        language: str = "UA",
        client_email: str | None = None,
        regular: RegularSpec | None = None,
    ) -> PurchaseForm:
        order_date = order_date if order_date is not None else int(time.time())
        amount_s = _fmt_amount(amount)

        signature = sign(
            self._secret,
            [
                self._account, self._domain, order_reference, str(order_date),
                amount_s, "UAH",
                product_name,   # productName[0]
                "1",            # productCount[0]
                amount_s,       # productPrice[0]
            ],
        )

        fields: dict[str, str | list[str]] = {
            "merchantAccount": self._account,
            "merchantDomainName": self._domain,
            "merchantSignature": signature,
            "orderReference": order_reference,
            "orderDate": str(order_date),
            "amount": amount_s,
            "currency": "UAH",
            "productName[]": [product_name],
            "productCount[]": ["1"],
            "productPrice[]": [amount_s],
            "language": language,
            "returnUrl": return_url,
            "serviceUrl": service_url,
        }
        if client_email:
            fields["clientEmail"] = client_email
        if regular is not None:
            fields.update(
                {
                    "regularMode": regular.mode,
                    "regularOn": "1",
                    # preset: the client cannot rewrite the schedule or the
                    # amount on the payment page. Without it they could set a
                    # yearly plan to renew daily — at the yearly price.
                    "regularBehavior": "preset",
                    "regularAmount": _fmt_amount(regular.amount),
                    "dateNext": regular.date_next.strftime("%d.%m.%Y"),
                }
            )
        return PurchaseForm(action=PURCHASE_URL, fields=fields)

    # --- callback ----------------------------------------------------------

    def verify_callback(self, payload: dict[str, object]) -> bool:
        """True when the signature matches AND the payment names our merchant."""
        received = str(payload.get("merchantSignature") or "")
        expected = sign(self._secret, [str(payload.get(f, "")) for f in _CALLBACK_SIGNATURE_FIELDS])
        if not hmac.compare_digest(received, expected):
            return False
        return str(payload.get("merchantAccount") or "") == self._account

    def read_callback(self, payload: dict[str, object]) -> CallbackResult:
        status = str(payload.get("transactionStatus") or "")
        rec_token = payload.get("recToken")
        return CallbackResult(
            order_reference=str(payload.get("orderReference") or ""),
            approved=status == APPROVED,
            amount=float(payload.get("amount") or 0),
            currency=str(payload.get("currency") or ""),
            transaction_status=status,
            reason=str(payload.get("reason") or ""),
            reason_code=str(payload.get("reasonCode") or ""),
            processing_date=str(payload.get("processingDate") or ""),
            auth_code=str(payload.get("authCode") or ""),
            rec_token=str(rec_token) if rec_token else None,
            raw=payload,
        )

    def callback_response(self, order_reference: str, *, status: str = "accept", now: int | None = None) -> dict:
        """The body WayForPay expects back. It retries the callback until it
        receives this — an unsigned or missing reply means the same charge
        arrives again and again."""
        moment = now if now is not None else int(time.time())
        return {
            "orderReference": order_reference,
            "status": status,
            "time": moment,
            "signature": sign(self._secret, [order_reference, status, str(moment)]),
        }

    # --- regular payments --------------------------------------------------

    async def _regular_request(self, request_type: str, order_reference: str, **extra) -> dict:
        payload = {
            "requestType": request_type,
            "merchantAccount": self._account,
            # NOT the secret key: the cabinet issues a distinct "Merchant
            # password" for this. regularApi authenticates by that value
            # directly rather than by a signature, unlike every other endpoint.
            "merchantPassword": self._password,
            "orderReference": order_reference,
            **extra,
        }
        if self._client is not None:
            resp = await self._client.post(REGULAR_API_URL, json=payload)
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(REGULAR_API_URL, json=payload)
        data = resp.json()
        # Responses come back UPPERCASED (REASONCODE/REASON), unlike every
        # other endpoint's camelCase.
        normalized = {str(k).lower(): v for k, v in data.items()}
        if str(normalized.get("reasoncode")) not in _REGULAR_OK_CODES:
            raise WayForPayError(
                f"{request_type} failed: {normalized.get('reason')} ({normalized.get('reasoncode')})"
            )
        return normalized

    async def suspend_regular(self, order_reference: str) -> dict:
        return await self._regular_request("SUSPEND", order_reference)

    async def remove_regular(self, order_reference: str) -> dict:
        return await self._regular_request("REMOVE", order_reference)

    async def regular_status(self, order_reference: str) -> dict:
        return await self._regular_request("STATUS", order_reference)
