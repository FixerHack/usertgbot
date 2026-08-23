"""Live-rate pricing: gross-up math, rounding, and NBU fetch/fallback."""

import httpx
import pytest

from shared import pricing


@pytest.fixture(autouse=True)
def _clear_rate_cache():
    pricing._cache.clear()
    yield
    pricing._cache.clear()


async def test_compute_prices_with_fixed_rate():
    # 100 UAH at 44.74 UAH/USD -> $2.2352 net
    result = await pricing.compute_prices(100, usd_uah_rate=44.74)
    assert result.profit_uah == 100
    assert result.usd_net == 2.2  # rounded to tenths (display only)
    # crypto invoice grossed for the 3% Crypto Pay fee: 2.2352 / 0.97, rounded UP
    assert result.usdt_invoice == pytest.approx(2.4, abs=0.05)
    # stars grossed for the ~$0.013/star net rate, then rounded UP to the
    # nearest 50 for a round number: 2.2352/0.013=171.9 -> 200
    assert result.stars == 200
    assert isinstance(result.stars, int)


async def test_gross_up_guarantees_profit_after_fee():
    result = await pricing.compute_prices(200, usd_uah_rate=44.74)
    # if we actually collected usdt_invoice and Crypto Pay takes its 3% cut,
    # what's left must be >= the USD-equivalent profit target
    net_received = result.usdt_invoice * (1 - pricing.CRYPTO_FEE)
    assert net_received >= result.usd_net - 0.05  # small tolerance for rounding


async def test_stars_are_whole_numbers_and_at_least_one():
    result = await pricing.compute_prices(1, usd_uah_rate=1000)  # tiny profit target
    assert result.stars >= 1
    assert isinstance(result.stars, int)


async def test_rounding_is_to_tenths():
    result = await pricing.compute_prices(137, usd_uah_rate=44.74)
    assert round(result.usd_net, 1) == result.usd_net
    assert round(result.usdt_invoice, 1) == result.usdt_invoice


async def test_get_rate_uses_nbu_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "bank.gov.ua" in str(request.url)
        return httpx.Response(200, json=[{"r030": 840, "txt": "US Dollar", "rate": 45.5, "cc": "USD"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rate = await pricing.get_usd_uah_rate(client=client)
    assert rate == 45.5


async def test_get_rate_caches(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[{"rate": 40.0}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r1 = await pricing.get_usd_uah_rate(client=client)
    r2 = await pricing.get_usd_uah_rate(client=client)
    assert r1 == r2 == 40.0
    assert calls["n"] == 1  # second call served from cache


async def test_get_rate_falls_back_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rate = await pricing.get_usd_uah_rate(client=client)
    assert rate == pricing._FALLBACK_USD_UAH
