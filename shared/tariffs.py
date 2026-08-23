"""Subscription tiers and the features/quotas each grants.

Single source of truth for both management_bot (sells them) and userbot
(enforces them). `profit_uah` is the FIXED net profit each tariff must yield
regardless of payment method — the actual amount charged in USDT or Stars is
computed at purchase time (shared.pricing.compute_prices) by grossing this
figure up for that channel's fee, so profit never varies with the payment
method or the exchange rate.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Tariff(str, enum.Enum):
    STANDARD = "standard"
    PRO = "pro"
    PREMIUM = "premium"


@dataclass(frozen=True)
class TariffPlan:
    tariff: Tariff
    title: str
    profit_uah: int            # guaranteed net profit, in UAH — see shared.pricing
    check_quota: int           # .check uses per calendar month; 0 = none
    has_autoresponder: bool
    features: list[str] = field(default_factory=list)
    available: bool = True     # False => shown as "in development", not purchasable

    @property
    def id(self) -> str:
        return self.tariff.value


PLANS: dict[Tariff, TariffPlan] = {
    Tariff.STANDARD: TariffPlan(
        tariff=Tariff.STANDARD,
        title="Standard",
        profit_uah=100,
        check_quota=5,
        has_autoresponder=False,
        features=[
            ".info — зводка по юзеру/чату",
            ".me — власна візитка",
            ".ban — бан + видалення",
            ".check — 5/місяць",
            "Автозбереження видалених/змінених повідомлень",
        ],
    ),
    Tariff.PRO: TariffPlan(
        tariff=Tariff.PRO,
        title="Pro",
        profit_uah=200,
        check_quota=10,
        has_autoresponder=True,
        features=[
            "Усе зі Standard",
            ".check — 10/місяць",
            "Автовідповідач (налаштовується)",
        ],
    ),
    Tariff.PREMIUM: TariffPlan(
        tariff=Tariff.PREMIUM,
        title="Premium",
        profit_uah=0,
        check_quota=0,
        has_autoresponder=False,
        features=["🚧 В розробці"],
        available=False,
    ),
}

# Commands available on any paid (available) tier.
BASE_COMMANDS = frozenset({"info", "me", "ban", "check"})


def get_plan(tariff: Tariff | str) -> TariffPlan:
    if isinstance(tariff, str):
        tariff = Tariff(tariff)
    return PLANS[tariff]


def purchasable_plans() -> list[TariffPlan]:
    return [p for p in PLANS.values() if p.available]


def tariff_grants_command(tariff: Tariff | str, command: str) -> bool:
    """Whether `command` (without the leading dot) is included in the tariff."""
    plan = get_plan(tariff)
    if not plan.available:
        return False
    if command == "autoresponder":
        return plan.has_autoresponder
    return command in BASE_COMMANDS
