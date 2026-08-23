"""Crypto Pay (@CryptoBot) integration.

Docs: https://help.crypt.bot/crypto-pay-api
We create a fiat-denominated invoice (the tariff price) that the user pays in
any accepted crypto, then poll its status. Async via httpx; the HTTP client is
injectable so it can be unit-tested without network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

MAINNET = "https://pay.crypt.bot/api"
TESTNET = "https://testnet-pay.crypt.bot/api"


@dataclass
class CryptoInvoice:
    invoice_id: str
    pay_url: str


class CryptoPayError(Exception):
    pass


class CryptoPayProvider:
    name = "crypto_pay"

    def __init__(self, token: str, *, testnet: bool = False, fiat: str = "USD", client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._base = TESTNET if testnet else MAINNET
        self._fiat = fiat
        self._client = client  # tests inject a mocked client

    def _headers(self) -> dict[str, str]:
        return {"Crypto-Pay-API-Token": self._token}

    async def _request(self, method: str, path: str, **kwargs):
        if self._client is not None:
            resp = await self._client.request(method, f"{self._base}{path}", headers=self._headers(), **kwargs)
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.request(method, f"{self._base}{path}", headers=self._headers(), **kwargs)
        data = resp.json()
        if not data.get("ok"):
            raise CryptoPayError(str(data.get("error")))
        return data["result"]

    async def create_invoice(self, *, amount: int, description: str, payload: str) -> CryptoInvoice:
        result = await self._request(
            "POST",
            "/createInvoice",
            json={
                "currency_type": "fiat",
                "fiat": self._fiat,
                "amount": str(amount),
                "description": description,
                "payload": payload,
                "expires_in": 3600,
            },
        )
        pay_url = result.get("bot_invoice_url") or result.get("pay_url") or result.get("mini_app_invoice_url", "")
        return CryptoInvoice(invoice_id=str(result["invoice_id"]), pay_url=pay_url)

    async def is_paid(self, invoice_id: str) -> bool:
        result = await self._request("GET", "/getInvoices", params={"invoice_ids": invoice_id})
        items = result.get("items", [])
        return bool(items) and items[0].get("status") == "paid"
