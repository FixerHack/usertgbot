"""Dummy provider that simulates a successful payment. For tests only,
until a real payment gateway is wired in.
"""

import uuid

from management_bot.payment.base import Invoice, PaymentProvider, PaymentStatus


class StubPayment(PaymentProvider):
    name = "stub"

    async def create_invoice(self, user_id: int, tariff_id: str, amount: int, currency: str) -> Invoice:
        # TODO: persist invoice, currently just fabricates an id
        return Invoice(id=str(uuid.uuid4()), amount=amount, currency=currency, payment_url=None)

    async def check_payment(self, invoice_id: str) -> PaymentStatus:
        # Always reports success — stand-in until a real gateway exists.
        return PaymentStatus(id=invoice_id, paid=True, raw_status="stub_success")

    async def refund(self, invoice_id: str) -> bool:
        return True
