"""Abstract payment provider interface.

Any real gateway (Stripe, LiqPay, Telegram Payments, crypto, ...) plugs in
by implementing this interface — handlers/subscribe.py depends only on it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Invoice:
    id: str
    amount: int
    currency: str
    payment_url: str | None = None


@dataclass
class PaymentStatus:
    id: str
    paid: bool
    raw_status: str


class PaymentProvider(ABC):
    @abstractmethod
    async def create_invoice(self, user_id: int, tariff_id: str, amount: int, currency: str) -> Invoice: ...

    @abstractmethod
    async def check_payment(self, invoice_id: str) -> PaymentStatus: ...

    @abstractmethod
    async def refund(self, invoice_id: str) -> bool: ...
