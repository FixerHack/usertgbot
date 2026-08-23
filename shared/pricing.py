"""Cross-channel pricing: guarantees a FIXED net profit (in UAH) regardless of
which payment method the user picks, by grossing up the charged amount to
absorb that channel's fee.

Live USD/UAH rate comes from the National Bank of Ukraine's official public
API (bank.gov.ua) — no API key, authoritative source for hryvnia rates,
refreshed on a short TTL so displayed/charged prices track the market without
hammering the endpoint on every carousel swipe. If the API is unreachable, a
dated fallback constant keeps purchases working rather than failing outright.

Fee constants (update the comments' date if the underlying deal changes):
  CRYPTO_FEE — Crypto Pay (@CryptoBot) app fee, from the app's own dashboard
    ("Fee: 3%*", checked 2026-08-22). Using the base 3%, not the discounted
    2.9%, so we never under-charge.
  STARS_NET_USD_PER_STAR — what a developer actually nets per Star after the
    Fragment withdrawal chain (Stars -> TON -> fiat), ~$13 per 1,000 Stars as
    of mid-2026 per multiple sources (see chat for links). This is the Stars
    counterpart of CRYPTO_FEE: how much of the face value survives cash-out.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

NBU_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode=USD&json"
_FALLBACK_USD_UAH = 44.74  # NBU rate observed 2026-08-22; used only if the API is down

CRYPTO_FEE = 0.03            # Crypto Pay app fee (source: CryptoBot dashboard, 2026-08-22)
STARS_NET_USD_PER_STAR = 0.013  # net USD per Star after Fragment withdrawal (2026)
STARS_ROUND_STEP = 50        # Stars price is rounded UP to a round number (nicer UX)

_CACHE_TTL_SECONDS = 3600
_cache: dict[str, tuple[float, float]] = {}  # {"usd_uah": (rate, fetched_at_monotonic)}


async def get_usd_uah_rate(*, client: httpx.AsyncClient | None = None) -> float:
    """Live USD->UAH rate (1 USD = N UAH), cached for an hour; falls back to a
    dated constant if the NBU API can't be reached."""
    cached = _cache.get("usd_uah")
    if cached is not None and (time.monotonic() - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    try:
        if client is not None:
            resp = await client.get(NBU_URL, timeout=10)
        else:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.get(NBU_URL)
        data = resp.json()
        rate = float(data[0]["rate"])
    except Exception:
        logger.warning("NBU rate fetch failed, using fallback %.2f", _FALLBACK_USD_UAH, exc_info=True)
        rate = _FALLBACK_USD_UAH

    _cache["usd_uah"] = (rate, time.monotonic())
    return rate


def _round_tenths(value: float) -> float:
    return round(value, 1)


def _ceil_tenths(value: float) -> float:
    """Round UP to one decimal — used for grossed-up charges, so rounding
    never eats into the profit guarantee (rounding to *nearest* tenths could
    leave the net a few cents short of the target)."""
    return math.ceil(value * 10) / 10


@dataclass(frozen=True)
class PriceBreakdown:
    profit_uah: int      # what we're guaranteed to net, in UAH
    usd_net: float        # profit_uah converted to USD, rounded to tenths
    usdt_invoice: float   # Crypto Pay invoice amount (USD/USDT) — grossed for CRYPTO_FEE
    stars: int            # Stars invoice amount — grossed for the Fragment withdrawal cut


async def compute_prices(profit_uah: int, *, usd_uah_rate: float | None = None) -> PriceBreakdown:
    rate = usd_uah_rate if usd_uah_rate is not None else await get_usd_uah_rate()
    usd_net = profit_uah / rate  # unrounded — everything below grosses up from this
    usdt_invoice = usd_net / (1 - CRYPTO_FEE)
    stars = usd_net / STARS_NET_USD_PER_STAR
    return PriceBreakdown(
        profit_uah=profit_uah,
        usd_net=_round_tenths(usd_net),
        # rounded UP: nearest-tenths could shave a few cents off the
        # guaranteed profit once the channel's fee is deducted
        usdt_invoice=_ceil_tenths(usdt_invoice),
        # rounded UP to a round number (STARS_ROUND_STEP) — nicer to look at
        # than e.g. 173⭐, and still guarantees the profit target since it
        # only ever rounds up from the exact grossed-up figure.
        stars=max(STARS_ROUND_STEP, math.ceil(stars / STARS_ROUND_STEP) * STARS_ROUND_STEP),
    )
