"""FastAPI app serving the Mini App connect page and completing the login.

Mirrors admin_web/app.py's shape (`create_app()` factory, injectable DB
dependency for tests) with one addition: `session_checker` is injectable too,
so tests can verify the whole POST /connect/{token}/session flow without a
real Telegram connection.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.sessions import StringSession

from db.models import User
from db.session import get_session
from management_bot import keyboards, storage
from shared import connect_tokens
from shared.device_identity import device_kwargs
from shared.i18n import resolve_lang, t
from shared.session_convert import gramjs_to_telethon
from shared.webapp_auth import verify_init_data

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

# Telegram Desktop and Telegram Web render Mini Apps in an IFRAME, so a
# blanket X-Frame-Options: DENY would break the whole thing; frame-ancestors
# scoped to Telegram is the correct equivalent. connect-src must allow the
# MTProto WebSocket gateways GramJS dials. telegram-web-app.js has to come
# from telegram.org — it provides initData, and Telegram already controls the
# WebView, so trusting their own script costs nothing. GramJS, by contrast, is
# self-hosted: a compromised third-party CDN there would hand over every
# user's account.
CSP = (
    "default-src 'none'; "
    "script-src 'self' https://telegram.org; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' wss://*.web.telegram.org https://*.web.telegram.org; "
    "img-src 'self' data:; "
    "frame-ancestors https://web.telegram.org https://*.telegram.org; "
    "base-uri 'none'; "
    "form-action 'none'"
)


async def _default_db() -> AsyncIterator[AsyncSession]:
    async with get_session() as session:
        yield session


SessionChecker = Callable[[str], Awaitable[dict]]


async def _default_session_checker(session_string: str, *, api_id: int, api_hash: str) -> dict:
    """Does the converted session actually work from THIS machine? Conversion
    producing a well-formed string proves nothing on its own — verified live
    during the prototype, this is the step that actually catches a bad DC
    mapping or a truncated key."""
    client = TelegramClient(StringSession(session_string), api_id, api_hash, **device_kwargs())
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("session converted but is NOT authorized")
        me = await client.get_me()
        return {
            "user_id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone,
        }
    finally:
        await client.disconnect()


class SessionPayload(BaseModel):
    initData: str
    dcId: int
    authKey: str


# Every __WA_*__ placeholder in index.html, mapped to its i18n key. Kept as an
# explicit table (rather than deriving the key from the placeholder) so a typo
# fails loudly in the round-trip test instead of silently leaving raw
# __WA_SOMETHING__ text on a page a client is looking at.
_PAGE_STRINGS = {
    "__WA_TITLE__": "wa_title",
    "__WA_OUTSIDE_TITLE__": "wa_outside_title",
    "__WA_OUTSIDE_BODY__": "wa_outside_body",
    "__WA_EXPIRED_TITLE__": "wa_expired_title",
    "__WA_EXPIRED_BODY__": "wa_expired_body",
    "__WA_CODE_TITLE__": "wa_code_title",
    "__WA_CODE_RETRY__": "wa_code_retry",
    "__WA_2FA_TITLE__": "wa_2fa_title",
    "__WA_2FA_BODY__": "wa_2fa_body",
    "__WA_2FA_SUBMIT__": "wa_2fa_submit",
    "__WA_2FA_NOTE__": "wa_2fa_note",
    "__WA_DONE_TITLE__": "wa_done_title",
    "__WA_DONE_BODY__": "wa_done_body",
}

# These land inside HTML *attributes* (data-* for the script, placeholder=
# for the password input), so their quotes must be escaped or they'd break
# out of the attribute.
_ATTR_STRINGS = {
    "__WA_2FA_PLACEHOLDER__": "wa_2fa_placeholder",
    "__WA_CODE_SENDING__": "wa_code_sending",
    "__WA_CODE_SENT__": "wa_code_sent",
    "__WA_CODE_PHONE__": "wa_code_phone",
    "__WA_CODE_SEND_FAILED__": "wa_code_send_failed",
    "__WA_CODE_EXPIRED__": "wa_code_expired",
    "__WA_CODE_TOO_SHORT__": "wa_code_too_short",
    "__WA_CODE_CHECKING__": "wa_code_checking",
    "__WA_RESUMING__": "wa_resuming",
    "__WA_ERROR__": "wa_error",
    "__WA_SERVER_REJECTED__": "wa_server_rejected",
    "__WA_2FA_CHECKING__": "wa_2fa_checking",
}


def _attr(value: str) -> str:
    """Escape for an HTML attribute value (the template quotes with ")."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _localize(template: str, lang: str) -> str:
    out = template.replace("__WA_LANG__", lang)
    for placeholder, key in _PAGE_STRINGS.items():
        out = out.replace(placeholder, t(lang, key))
    for placeholder, key in _ATTR_STRINGS.items():
        # NOT t(..., **kwargs): these keep their {phone}/{error} placeholders
        # for the browser to fill in at runtime (see app.js's tr()).
        out = out.replace(placeholder, _attr(t(lang, key)))
    return out


async def _owner_lang(db: AsyncSession, telegram_id: int) -> str:
    row = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
    return resolve_lang(row.language_code if row else None)


def create_app(
    *,
    bot_token: str,
    webapp_api_id: int | None,
    webapp_api_hash: str | None,
    manager_bot_username: str | None = None,
    db_dependency=_default_db,
    session_checker: SessionChecker | None = None,
) -> FastAPI:
    checker = session_checker or (
        lambda s: _default_session_checker(s, api_id=webapp_api_id, api_hash=webapp_api_hash)
    )

    app = FastAPI(title="usertgbot connect", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        # Middleware on this app also runs for the sub-apps mounted on it
        # (/pay, and the landing), whose policies are deliberately different —
        # the payment page has to POST a form to the gateway, which this CSP
        # forbids. So this acts as the default only: a sub-app that already
        # set its own header keeps it.
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/connect/{token}", response_class=HTMLResponse)
    async def page(token: str, db: AsyncSession = Depends(db_dependency)) -> HTMLResponse:
        row = await connect_tokens.get_valid_token(db, token)
        template = (STATIC / "index.html").read_text(encoding="utf-8")

        # An expired/unknown token still renders a page, so it still needs its
        # language — but there's no token row to look the owner up by, so fall
        # back to the default rather than showing a half-translated page.
        lang = "uk" if row is None else await _owner_lang(db, row.telegram_id)
        template = _localize(template, lang)

        if row is None:
            return HTMLResponse(template.replace('data-expired="false"', 'data-expired="true"'))
        html = (
            template
            .replace("__TOKEN__", token)
            .replace("__PHONE__", _attr(row.phone))
            .replace("__EXPIRES_AT__", row.expires_at.isoformat() + "Z")
            # The countdown ring needs a full-scale reference; sending the real
            # TTL keeps it correct if DEFAULT_TTL_MINUTES ever changes.
            .replace("__TTL_SECONDS__", str(connect_tokens.DEFAULT_TTL_MINUTES * 60))
        )
        return HTMLResponse(html)

    @app.get("/app.js")
    async def app_js() -> Response:
        return Response((STATIC / "app.js").read_text(encoding="utf-8"), media_type="application/javascript")

    @app.get("/gramjs.js")
    async def gramjs() -> FileResponse:
        path = STATIC / "gramjs.js"
        if not path.exists():
            raise HTTPException(status_code=500, detail="gramjs.js missing — run build_gramjs.sh")
        return FileResponse(path, media_type="application/javascript")

    @app.get("/webapp-config")
    async def webapp_config() -> dict:
        # Public by design — see the comment on ManagementBotSettings.webapp_api_id.
        return {"api_id": webapp_api_id, "api_hash": webapp_api_hash}

    @app.post("/connect/{token}/session")
    async def complete_session(
        token: str, payload: SessionPayload, db: AsyncSession = Depends(db_dependency)
    ) -> JSONResponse:
        row = await connect_tokens.get_valid_token(db, token)
        if row is None:
            raise HTTPException(status_code=410, detail="token expired or already used")

        try:
            # The default 300s freshness window assumes the Mini App is
            # opened and finished in one quick motion. Here it can legitimately
            # sit open the whole token TTL — thinking time, waiting for the
            # Telegram code to arrive, entering 2FA — and initData is captured
            # once at launch and never refreshes while the WebView stays open.
            # Confirmed live: a real login failed at 778s with the 300s
            # default even though the token itself was still valid. Match the
            # window to what the token already allows, not a shorter guess.
            user = verify_init_data(
                payload.initData, bot_token, max_age_seconds=connect_tokens.DEFAULT_TTL_MINUTES * 60 + 120
            )
        except ValueError as exc:
            logger.warning("initData rejected for token=%s: %s", token, exc)
            raise HTTPException(status_code=401, detail=f"initData rejected: {exc}")

        # The signature proves WHICH Telegram user opened the page — it must be
        # the same person the token was issued to, or someone who intercepted a
        # forwarded link could attach a session to their own account instead.
        if user.telegram_id != row.telegram_id:
            logger.warning(
                "telegram_id mismatch for token=%s: token=%s initData=%s",
                token, row.telegram_id, user.telegram_id,
            )
            raise HTTPException(status_code=401, detail="initData does not match this token's owner")

        try:
            session_string = gramjs_to_telethon(payload.dcId, payload.authKey)
        except ValueError as exc:
            logger.warning("session conversion failed for token=%s: %s", token, exc)
            raise HTTPException(status_code=400, detail=f"conversion failed: {exc}")

        try:
            account = await checker(session_string)
        except Exception as exc:  # noqa: BLE001 - surface whatever Telethon raised
            logger.exception("server-side session check failed for token=%s", token)
            raise HTTPException(status_code=400, detail=f"session check failed: {exc}")

        # Consume ONLY after everything else succeeded — a failed attempt (bad
        # initData, bad conversion, dead session) must leave the token alive so
        # the user can retry within their 15 minutes, not burn their one shot on
        # a transient error.
        consumed = await connect_tokens.consume_token(db, token)
        if consumed is None:
            # Lost a race with a concurrent request for the same token — the
            # other one already saved a session, so don't save a second.
            raise HTTPException(status_code=410, detail="token already used")

        await storage.save_session(
            db,
            telegram_id=row.telegram_id,
            phone_number=row.phone,
            session_string=session_string,
            username=account.get("username"),
            full_name=" ".join(p for p in (account.get("first_name"), account.get("last_name")) if p) or None,
        )
        await db.commit()
        logger.info("connect_web: session saved for telegram_id=%s (token=%s)", row.telegram_id, token)

        lang = await _owner_lang(db, row.telegram_id)

        await _finish_in_chat(bot_token, row, account, lang=lang, manager_bot_username=manager_bot_username)

        return JSONResponse({"ok": True, "account": account})

    return app


async def _finish_in_chat(
    bot_token: str, row, account: dict, *, lang: str, manager_bot_username: str | None
) -> None:
    """Remove the Mini App button (so it can't be tapped again) and confirm in
    the chat — the page says "connected" and then gets closed, so without a
    message in the chat the user has no lasting evidence it worked. Both are
    best-effort: the session is already saved, a messaging hiccup here must
    never make a successful connection look failed.

    The manager-bot nudge mirrors the old flow's `_persist_and_finish`
    (management_bot/handlers/connect.py) exactly, same i18n keys — that
    message was specifically redesigned earlier (own message, bold heading,
    a real button) because buried as a trailing text line most users missed
    it. The new flow must not quietly drop that lesson.
    """
    name = account.get("username")
    who = f"@{name}" if name else str(account.get("user_id"))
    text = f"{t(lang, 'connect_success')}\n\n👤 {who}"
    # Same reply keyboard the old flow's _persist_and_finish set on this exact
    # message — without it, the one-time "share phone" keyboard from before
    # the Mini App opened just keeps showing (Telegram never clears a reply
    # keyboard on its own; only an explicit new reply_markup replaces it).
    # Reused from management_bot.keyboards, not hand-duplicated, so it can't
    # silently drift from what the rest of the bot actually uses.
    main_menu_kb = keyboards.main_menu(lang).model_dump(mode="json", exclude_none=True)

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            if row.message_id:
                await http.post(
                    f"https://api.telegram.org/bot{bot_token}/deleteMessage",
                    json={"chat_id": row.chat_id, "message_id": row.message_id},
                )
            resp = await http.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": row.chat_id, "text": text, "parse_mode": "HTML", "reply_markup": main_menu_kb},
            )
            if resp.status_code != 200:
                logger.warning("connect_web: chat confirmation failed: %s", resp.text[:200])

            if manager_bot_username:
                manager = f"@{manager_bot_username}"
                kb = {
                    "inline_keyboard": [[
                        {"text": t(lang, "start_manager_btn"), "url": f"https://t.me/{manager_bot_username}"}
                    ]]
                }
                hint_resp = await http.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": row.chat_id,
                        "text": t(lang, "start_manager_hint", manager=manager),
                        "parse_mode": "HTML",
                        "reply_markup": kb,
                    },
                )
                if hint_resp.status_code != 200:
                    logger.warning("connect_web: manager-bot hint failed: %s", hint_resp.text[:200])
    except Exception:
        logger.exception("connect_web: failed to finish in chat for telegram_id=%s", row.telegram_id)
