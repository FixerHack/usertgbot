"""Serves the one-page site, with live prices filled in at render time.

Prices come from the same `shared.tariffs` + `shared.pricing` the bot sells
from, so the site cannot quote a figure the bot no longer charges — a
mismatch the acquirer's moderation would (rightly) treat as misleading.

The seller's registration details are deliberately NOT here: WayForPay shows
them on its own merchant page during checkout, which the acquirer confirmed is
sufficient. Publishing a private individual's name, tax id and home address on
an open page is a real cost with no gain, so the page names the support contact
and points at the merchant page for the rest.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse

from shared.pricing import compute_prices
from shared.tariffs import DURATIONS, PLANS, Duration, discount_pct, effective_profit_uah, get_plan
from shared.i18n import features

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

# Self-contained: no external fonts, scripts or images, so the whole policy is
# 'self' plus inline styles. The page has no form of its own.
CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'none'"
)


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


async def _price_rows() -> str:
    """One table row per tariff, one cell per duration — the same arithmetic
    the tariff carousel in the bot uses."""
    rows = []
    for tariff in PLANS:
        plan = get_plan(tariff)
        cells = []
        for code, opt in DURATIONS.items():
            prices = await compute_prices(int(effective_profit_uah(plan.profit_uah, opt, 0)))
            cells.append(f"<td><b>{prices.uah_invoice}</b> ₴</td>")
        bullets = "".join(f"<li>{_esc(f)}</li>" for f in features("uk", plan.id))
        rows.append(
            f'<tr><th scope="row"><span class="plan">{_esc(plan.title)}</span>'
            f"<ul class=\"bullets\">{bullets}</ul></th>{''.join(cells)}</tr>"
        )
    return "".join(rows)


def _duration_headers() -> str:
    labels = {Duration.MONTH: "місяць", Duration.QUARTER: "3 місяці", Duration.YEAR: "рік"}
    cells = []
    for code, opt in DURATIONS.items():
        pct = discount_pct(opt)
        badge = f'<span class="badge">−{pct}%</span>' if pct > 0 else ""
        cells.append(f"<th>{labels[code]}{badge}</th>")
    return "".join(cells)


def create_app(
    *,
    bot_username: str | None = None,
    support_contact: str | None = None,
) -> FastAPI:
    app = FastAPI(title="usertgbot site", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/site.js")
    async def script() -> FileResponse:
        return FileResponse(STATIC / "site.js", media_type="application/javascript")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        template = (STATIC / "index.html").read_text(encoding="utf-8")
        bot_link = f"https://t.me/{bot_username}" if bot_username else "#tariffs"
        html_out = (
            template
            .replace("__PRICE_HEADERS__", _duration_headers())
            .replace("__PRICE_ROWS__", await _price_rows())
            .replace("__BOT_LINK__", _esc(bot_link))
            .replace("__BOT_NAME__", _esc(f"@{bot_username}" if bot_username else "бот"))
            .replace("__SUPPORT__", _esc(support_contact or "—"))
        )
        return HTMLResponse(html_out)

    return app
