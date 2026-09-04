from management_bot.payment.base import PaymentProvider
from management_bot.payment.stub import StubPayment
from management_bot.payment.wayforpay import WayForPayProvider

__all__ = ["PaymentProvider", "StubPayment", "WayForPayProvider", "build_wayforpay"]


def build_wayforpay() -> WayForPayProvider | None:
    """The configured card gateway, or None when it isn't set up.

    Lives here rather than in wayforpay.py so that module stays free of any
    config import and can be unit-tested with nothing but its own arguments.
    """
    from management_bot.config import settings

    if not settings.wayforpay_configured:
        return None
    return WayForPayProvider(
        settings.wayforpay_merchant_account,
        settings.wayforpay_secret_key,
        settings.wayforpay_domain,
        merchant_password=settings.wayforpay_merchant_password,
    )
