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
    has_send: bool = False      # .send — bulk-post N copies of a message
    send_cooldown_seconds: int = 0   # min gap between .send runs; meaningless if has_send is False
    send_max_count: int = 0          # max copies per .send call; meaningless if has_send is False
    features: list[str] = field(default_factory=list)
    available: bool = True     # False => shown as "in development", not purchasable

    @property
    def id(self) -> str:
        return self.tariff.value


PLANS: dict[Tariff, TariffPlan] = {
    Tariff.STANDARD: TariffPlan(
        tariff=Tariff.STANDARD,
        title="Standard",
        profit_uah=50,
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
        profit_uah=150,
        check_quota=10,
        has_autoresponder=True,
        has_send=True,
        send_cooldown_seconds=600,   # 10 min
        send_max_count=50,
        features=[
            "Усе зі Standard",
            ".check — 10/місяць",
            "Автовідповідач (налаштовується)",
            ".send — масове надсилання (до 50 повідомлень, раз на 10 хв)",
        ],
    ),
    Tariff.PREMIUM: TariffPlan(
        tariff=Tariff.PREMIUM,
        title="Premium",
        profit_uah=0,
        check_quota=0,
        has_autoresponder=False,
        has_send=True,
        send_cooldown_seconds=120,   # 2 min
        send_max_count=100,
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
    if command == "send":
        return plan.has_send
    return command in BASE_COMMANDS


class Duration(str, enum.Enum):
    MONTH = "1m"
    QUARTER = "3m"
    YEAR = "1y"


@dataclass(frozen=True)
class DurationOption:
    code: Duration
    days: int
    multiplier: int        # multiplies profit_uah for this period
    nominal_months: int    # "fair" month-count, for the discount badge below


# 1 and 3 months charge exactly ×1/×3 of the monthly price (no discount). A
# year charges ×10 instead of the "fair" ×12 — nominal_months captures that
# fair figure so discount_pct() below can derive the badge (~17% off).
DURATIONS: dict[Duration, DurationOption] = {
    Duration.MONTH: DurationOption(Duration.MONTH, 30, 1, 1),
    Duration.QUARTER: DurationOption(Duration.QUARTER, 90, 3, 3),
    Duration.YEAR: DurationOption(Duration.YEAR, 365, 10, 12),
}


def get_duration(code: Duration | str) -> DurationOption:
    if isinstance(code, str):
        code = Duration(code)
    return DURATIONS[code]


def discount_pct(opt: DurationOption) -> int:
    """0 when a duration charges its full nominal price (month/quarter);
    positive when it's grossed down from that (year)."""
    if opt.nominal_months <= 0:
        return 0
    return round((1 - opt.multiplier / opt.nominal_months) * 100)


def effective_profit_uah(base_profit_uah: float, opt: DurationOption, referral_discount_pct: int = 0) -> float:
    """The price to actually charge for `opt`, given a referral discount.

    The two discounts never stack — a year already prices at -17% off its
    nominal (×10 instead of ×12); a referral discount on top of that would
    compound into a bigger cut than either was meant to give alone. Instead,
    whichever discount is bigger wins outright, the other simply isn't
    applied too. For month/quarter (no built-in discount of their own) this
    reduces to just the referral discount, same as before.
    """
    duration_only = base_profit_uah * opt.multiplier
    referral_only = base_profit_uah * opt.nominal_months * (1 - referral_discount_pct / 100)
    return min(duration_only, referral_only)
