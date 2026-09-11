"""The public site.

The part that is not cosmetic: the prices must be the ones the bot actually
charges. A site quoting a stale figure is the kind of thing an acquirer treats
as misleading and a buyer treats as a bait-and-switch, and it drifts silently
the moment someone types a number into the HTML.
"""

from __future__ import annotations

import pytest

from landing.app import create_app

SUPPORT = "@ua_support"


async def _client(**kwargs):
    from httpx import ASGITransport, AsyncClient

    app = create_app(**kwargs)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_prices_come_from_the_tariffs_the_bot_sells():
    """Hardcoding them in the HTML would let the site and the bot drift apart
    silently — the site is what a buyer reads before paying."""
    from shared.pricing import compute_prices
    from shared.tariffs import DURATIONS, PLANS, effective_profit_uah, get_plan

    async with await _client(support_contact=SUPPORT) as ac:
        page = (await ac.get("/")).text

    for tariff in PLANS:
        plan = get_plan(tariff)
        for _, opt in DURATIONS.items():
            expected = (await compute_prices(int(effective_profit_uah(plan.profit_uah, opt, 0)))).uah_invoice
            assert f"<b>{expected}</b> ₴" in page, f"{plan.title}/{opt.days}d missing from the price table"


async def test_every_plan_and_its_features_are_listed():
    from shared.i18n import features
    from shared.tariffs import PLANS, get_plan

    async with await _client(support_contact=SUPPORT) as ac:
        page = (await ac.get("/")).text

    for tariff in PLANS:
        plan = get_plan(tariff)
        assert plan.title in page
        assert features("uk", plan.id)[0] in page


async def test_support_contact_is_reachable_from_the_offer():
    """The refund clause tells people where to write; if the contact ever
    stops rendering, that clause points nowhere."""
    async with await _client(support_contact=SUPPORT) as ac:
        page = (await ac.get("/")).text
    assert page.count(SUPPORT) >= 2


async def test_no_private_person_details_are_published():
    """Registration details live on WayForPay's own merchant page, which the
    acquirer accepts. Publishing a private individual's name, tax id and home
    address on an open page is a cost with no gain."""
    async with await _client(support_contact=SUPPORT) as ac:
        page = (await ac.get("/")).text
    for leaked in ("ЄДРПОУ", "ІПН", "ФОП "):
        assert leaked not in page


@pytest.mark.parametrize(
    "needle",
    [
        "Повернення коштів",          # refund terms
        "Обробка персональних даних",  # privacy
        "Реквізити продавця",          # where to find them
        "Скасувати автопродовження",   # how to stop a standing mandate
        "UAH",                         # settlement currency
    ],
)
async def test_page_carries_what_moderation_looks_for(needle):
    async with await _client(support_contact=SUPPORT) as ac:
        page = (await ac.get("/")).text
    assert needle in page


async def test_script_is_external_so_the_page_needs_no_inline_csp_exception():
    async with await _client(support_contact=SUPPORT) as ac:
        page = await ac.get("/")
        script = await ac.get("/site.js")

    assert "<script src=\"/site.js\">" in page.text
    assert script.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]


async def test_theme_toggle_survives_blocked_storage():
    """A private window makes localStorage throw on access, not return null.
    An unguarded read there would kill the script and leave a dead button."""
    from pathlib import Path

    import landing.app as mod

    source = (Path(mod.STATIC) / "site.js").read_text(encoding="utf-8")
    assert source.count("try {") >= 2


async def test_bot_link_points_at_the_bot_when_configured():
    async with await _client(bot_username="useagentperbot", support_contact=SUPPORT) as ac:
        page = (await ac.get("/")).text
    assert "https://t.me/useagentperbot" in page


async def test_root_mount_does_not_swallow_connect_or_pay(db_session):
    """The landing is mounted at "/" and would match everything; only
    registration order keeps the Mini App and the payment page reachable.
    That ordering lives in connect_web/server.py and is easy to lose."""
    from httpx import ASGITransport, AsyncClient

    from connect_web.app import create_app as create_connect_app
    from management_bot.payment.wayforpay import (
        TEST_MERCHANT_ACCOUNT,
        TEST_MERCHANT_SECRET,
        WayForPayProvider,
    )
    from pay_web.app import create_app as create_pay_app

    async def _db():
        yield db_session

    root = create_connect_app(
        bot_token="8123456:AAF-test-token-value", webapp_api_id=1, webapp_api_hash="h",
        db_dependency=_db,
    )
    root.mount("/pay", create_pay_app(
        provider=WayForPayProvider(TEST_MERCHANT_ACCOUNT, TEST_MERCHANT_SECRET, "moozuku.tech"),
        base_url=lambda: "https://x", db_dependency=_db,
    ))
    root.mount("/", create_app(support_contact=SUPPORT))

    async with AsyncClient(transport=ASGITransport(app=root), base_url="http://t") as ac:
        home = await ac.get("/")
        connect = await ac.get("/connect/whatever")
        pay = await ac.get("/pay/sub-1-1")

    assert "Оферта" in home.text
    assert 'data-expired="true"' in connect.text, "the Mini App page must still answer"
    assert pay.status_code == 404 and "<h1>" in pay.text, "the payment app must still answer"
