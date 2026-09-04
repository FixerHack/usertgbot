"""Card-payment pages and the WayForPay callback.

Three routes, mounted under /pay:

  GET  /{order_reference}      the page that POSTs the signed form to the gateway
  GET  /done                   where the gateway sends the browser back
  POST /wayforpay/callback     server-to-server result — the only source of truth

The browser never decides whether a payment happened: `returnUrl` is decoration,
the callback is what activates a subscription. That split matters because the
user can close the tab, and because a `returnUrl` hit is trivially forgeable.

The page submits itself. WayForPay's purchase endpoint takes a POST, and the
invoice API that would give us a plain link cannot carry a recurring schedule —
so a page of ours is unavoidable, but a tap on it is not. The visible button
stays as the fallback for scripting being off.
"""

from __future__ import annotations

import html
import logging
from collections.abc import AsyncIterator, Callable
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SubscriptionStatus, User
from db.session import get_session
from management_bot import subscriptions
from management_bot.payment.wayforpay import (
    RegularSpec,
    WayForPayProvider,
    parse_callback_body,
)
from shared.i18n import resolve_lang, t
from shared.tariffs import get_plan

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

# form-action must name the gateway explicitly: the whole page exists to POST
# somewhere else. Everything else stays shut — there is no script on the page,
# so script-src is 'none' rather than 'self'.
# telegram.org is allowed for one reason: opened as a Mini App, the page after
# payment offers to close itself, and that needs Telegram's own script. It is
# their WebView already, so trusting their script there costs nothing.
# frame-ancestors permits Telegram, which renders Mini Apps in an iframe on
# Desktop and Web — 'none' would break the whole thing there.
CSP = (
    "default-src 'none'; "
    "script-src 'self' https://telegram.org; "
    "style-src 'unsafe-inline'; "
    "img-src 'self' data:; "
    "base-uri 'none'; "
    "frame-ancestors https://web.telegram.org https://*.telegram.org; "
    "form-action https://secure.wayforpay.com"
)

# WayForPay's own vocabulary for a renewal period, keyed by the number of days
# the tariff duration covers. A duration with no equivalent here simply cannot
# be sold as auto-renewing (the caller falls back to a one-off charge) — better
# than rounding a 90-day period to "monthly" and charging four times a year.
REGULAR_MODE_BY_DAYS = {30: "monthly", 90: "quarterly", 365: "yearly"}


async def _default_db() -> AsyncIterator[AsyncSession]:
    async with get_session() as session:
        yield session


def _page(body: str, *, script: str = "", telegram: bool = False) -> str:
    """`telegram` pulls in Telegram's Mini App script, which is only useful on
    the page that offers to close the WebView."""
    head = '<script src="https://telegram.org/js/telegram-web-app.js"></script>' if telegram else ""
    return (
        (STATIC / "pay.html").read_text(encoding="utf-8")
        .replace("__BODY__", body)
        .replace("__HEAD__", head)
        .replace("__SCRIPT__", script)
    )


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _hidden_inputs(fields: dict[str, str | list[str]]) -> str:
    rows = []
    for name, value in fields.items():
        values = value if isinstance(value, list) else [value]
        rows.extend(f'<input type="hidden" name="{_esc(name)}" value="{_esc(v)}">' for v in values)
    return "\n".join(rows)


def create_app(
    *,
    provider: WayForPayProvider | None = None,
    base_url: Callable[[], str | None] = lambda: None,
    db_dependency=_default_db,
    notifier=None,
    bot_username: str | None = None,
) -> FastAPI:
    """`provider` is None when WayForPay isn't configured — the routes still
    exist and answer politely rather than 404-ing, so a stale payment link
    never shows a raw error page."""

    app = FastAPI(title="usertgbot pay", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def _lang_of(db: AsyncSession, sub) -> str:
        user = await db.get(User, sub.user_id)
        return resolve_lang(user.language_code if user is not None else None)

    # GET *and* POST: WayForPay posts the result to returnUrl by default (the
    # cabinet has a toggle to disable it). Accepting both means the page works
    # either way instead of greeting a paying customer with a 405.
    @app.get("/pay.js")
    async def pay_script() -> FileResponse:
        return FileResponse(STATIC / "pay.js", media_type="application/javascript")

    @app.get("/done.js")
    async def done_script() -> FileResponse:
        return FileResponse(STATIC / "done.js", media_type="application/javascript")

    @app.api_route("/done", methods=["GET", "POST"], response_class=HTMLResponse)
    async def done(lang: str = "uk") -> HTMLResponse:
        # The gateway sends the browser here whatever the outcome, so this page
        # deliberately claims nothing about success — the callback decides, and
        # the bot delivers the verdict. Nothing posted here is trusted.
        lang = resolve_lang(lang)
        body = (
            f"<h1>{_esc(t(lang, 'pay_done_title'))}</h1>"
            f"<p>{_esc(t(lang, 'pay_done_text'))}</p>"
            f'<button class="btn" id="close-app" hidden>{_esc(t(lang, "pay_close"))}</button>'
        )
        if bot_username:
            body += f'<p><a class="btn ghost" href="https://t.me/{_esc(bot_username)}">@{_esc(bot_username)}</a></p>'
        return HTMLResponse(_page(body, telegram=True, script='<script src="/pay/done.js"></script>'))

    @app.get("/{order_reference}", response_class=HTMLResponse)
    async def pay_page(order_reference: str, db: AsyncSession = Depends(db_dependency)) -> HTMLResponse:
        sub = await subscriptions.get_by_order_reference(db, order_reference)
        lang = "uk" if sub is None else await _lang_of(db, sub)

        if provider is None or sub is None or sub.amount_uah is None:
            return HTMLResponse(
                _page(f"<h1>{_esc(t(lang, 'pay_expired_title'))}</h1><p>{_esc(t(lang, 'pay_expired_text'))}</p>"),
                status_code=404,
            )
        if sub.status is SubscriptionStatus.ACTIVE:
            return HTMLResponse(
                _page(f"<h1>{_esc(t(lang, 'pay_already_title'))}</h1><p>{_esc(t(lang, 'pay_already_text'))}</p>")
            )

        base = (base_url() or "").rstrip("/")
        plan = get_plan(sub.tariff)
        product = t(lang, "pay_product", title=plan.title, days=sub.period_days)

        regular = None
        if sub.auto_renew:
            mode = REGULAR_MODE_BY_DAYS.get(sub.period_days)
            if mode is not None:
                regular = RegularSpec(
                    mode=mode,
                    amount=sub.amount_uah,
                    date_next=date.today() + timedelta(days=sub.period_days),
                )

        form = provider.build_purchase(
            order_reference=sub.order_reference,
            amount=sub.amount_uah,
            product_name=product,
            return_url=f"{base}/pay/done?lang={lang}",
            service_url=f"{base}/pay/wayforpay/callback",
            language={"uk": "UA", "ru": "RU", "en": "EN"}.get(lang, "UA"),
            regular=regular,
        )

        note = t(lang, "pay_autorenew_note") if regular is not None else t(lang, "pay_once_note")
        body = (
            f"<h1>{_esc(t(lang, 'pay_title'))}</h1>"
            f"<p class=\"product\">{_esc(product)}</p>"
            f"<p class=\"amount\">{_esc(sub.amount_uah)} ₴</p>"
            f"<p class=\"note\">{_esc(note)}</p>"
            f'<form id="pay-form" method="POST" action="{_esc(form.action)}" accept-charset="utf-8">'
            f"{_hidden_inputs(form.fields)}"
            f'<button class="btn" type="submit">{_esc(t(lang, "pay_button"))}</button>'
            "</form>"
        )
        return HTMLResponse(_page(body, script='<script src="/pay/pay.js"></script>'))

    @app.post("/wayforpay/callback")
    async def callback(request: Request, db: AsyncSession = Depends(db_dependency)) -> JSONResponse:
        if provider is None:
            return JSONResponse({"error": "not configured"}, status_code=503)

        raw = await request.body()
        try:
            payload = parse_callback_body(raw)
        except ValueError:
            logger.warning("wayforpay callback: unparseable body")
            return JSONResponse({"error": "bad request"}, status_code=400)

        if not provider.verify_callback(payload):
            # Deliberately NOT an "accept": a body we can't authenticate must
            # never be acknowledged, or a forged one would stop the real
            # callback from ever being retried.
            logger.warning("wayforpay callback: signature mismatch for %s", payload.get("orderReference"))
            return JSONResponse({"error": "bad signature"}, status_code=400)

        result = provider.read_callback(payload)
        sub = await subscriptions.get_by_order_reference(db, result.order_reference)

        event = await subscriptions.record_payment_event(
            db,
            provider=provider.name,
            order_reference=result.order_reference,
            auth_code=result.auth_code,
            processing_date=result.processing_date,
            amount=result.amount,
            currency=result.currency,
            status=result.transaction_status,
            subscription_id=sub.id if sub is not None else None,
        )
        if event is None:
            # Already applied. Still answer "accept", or the gateway keeps
            # re-delivering the charge it has already been credited for.
            await db.commit()
            return JSONResponse(provider.callback_response(result.order_reference))

        if sub is None:
            logger.warning("wayforpay callback: no subscription for %s", result.order_reference)
            await db.commit()
            return JSONResponse(provider.callback_response(result.order_reference))

        renewal = sub.status is SubscriptionStatus.ACTIVE
        if result.approved and _amount_matches(result.amount, sub.amount_uah):
            if renewal:
                await subscriptions.extend(db, sub)
            else:
                await subscriptions.activate(db, sub)
            await _notify(db, sub, "paid", notifier)
        elif result.approved:
            # Signed, but not for the sum we asked. Nothing is granted; this
            # is either a gateway-side change or our own bug, and both need a
            # human to look rather than a silent activation.
            logger.error(
                "wayforpay callback: amount mismatch on %s — charged %s, expected %s",
                result.order_reference, result.amount, sub.amount_uah,
            )
        else:
            logger.info(
                "wayforpay callback: %s declined (%s / %s)",
                result.order_reference, result.transaction_status, result.reason,
            )
            if renewal:
                await _notify(db, sub, "renew_failed", notifier)

        await db.commit()
        return JSONResponse(provider.callback_response(result.order_reference))

    return app


def _amount_matches(charged: float, expected: int | None) -> bool:
    if expected is None:
        return False
    return abs(charged - float(expected)) < 0.01


async def _notify(db: AsyncSession, sub, kind: str, notifier) -> None:
    """Best-effort DM about the payment. A failure here must never fail the
    callback — the money moved either way, and an unacknowledged callback
    gets re-delivered forever."""
    if notifier is None:
        return
    try:
        user = await db.get(User, sub.user_id)
        if user is None:
            return
        lang = resolve_lang(getattr(user, "language_code", None))
        title = get_plan(sub.tariff).title
        if kind == "paid":
            text = t(lang, "sub_success", title=title) + "\n" + t(lang, "sub_connect_hint")
        else:
            text = t(lang, "pay_renew_failed", title=title)
        await notifier.send_text(user.telegram_id, text)
    except Exception:
        logger.exception("wayforpay: notify failed for subscription %s", sub.id)
