"""connect_web's FastAPI app — the whole Mini App handoff, without a real
Telegram connection.

`session_checker` is injected (mirrors `db_dependency` in admin_web's tests)
so the happy path is exercised end to end: token validation, initData
signature + ownership check, session conversion, the atomic single-use
guarantee, and the real `management_bot.storage.save_session` write.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import b64encode
from urllib.parse import urlencode

from db.models import Session
from shared.connect_tokens import create_token
from shared.session_convert import gramjs_to_telethon

BOT_TOKEN = "8123456:AAF-test-token-value"


def _init_data(telegram_id: int, *, token: str = BOT_TOKEN, age: int = 0) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age),
        "user": json.dumps({"id": telegram_id, "username": "tester"}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _fake_auth_key() -> str:
    import os

    return b64encode(os.urandom(256)).decode()


async def _fake_checker_ok(session_string: str) -> dict:
    return {"user_id": 724233980, "username": "fixerhack", "first_name": "Danya", "last_name": None}


async def _client_and_app(db_session, *, session_checker=None):
    from httpx import ASGITransport, AsyncClient

    from connect_web.app import create_app

    async def _db():
        yield db_session

    app = create_app(
        bot_token=BOT_TOKEN, webapp_api_id=1, webapp_api_hash="hash",
        db_dependency=_db, session_checker=session_checker or _fake_checker_ok,
    )
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://t")


async def test_unknown_token_is_404(db_session):
    async with await _client_and_app(db_session) as ac:
        r = await ac.get("/connect/does-not-exist")
        assert r.status_code == 200  # server-rendered "expired" page, not a bare 404
        assert 'data-expired="true"' in r.text


async def test_valid_token_serves_the_form(db_session):
    token = await create_token(db_session, telegram_id=724233980, chat_id=724233980, phone="+380671234567")
    await db_session.commit()
    async with await _client_and_app(db_session) as ac:
        r = await ac.get(f"/connect/{token.token}")
        assert r.status_code == 200
        assert 'data-expired="false"' in r.text
        assert "+380671234567" in r.text
        assert token.token in r.text


async def test_webapp_config_exposes_the_public_pair(db_session):
    async with await _client_and_app(db_session) as ac:
        r = await ac.get("/webapp-config")
        assert r.json() == {"api_id": 1, "api_hash": "hash"}


async def test_session_post_rejects_bad_signature(db_session):
    token = await create_token(db_session, telegram_id=724233980, chat_id=724233980, phone="+1")
    await db_session.commit()
    async with await _client_and_app(db_session) as ac:
        r = await ac.post(
            f"/connect/{token.token}/session",
            json={"initData": "", "dcId": 2, "authKey": _fake_auth_key()},
        )
        assert r.status_code == 401


async def test_session_post_rejects_mismatched_owner(db_session):
    """The attack that matters most: a valid signature, but for a DIFFERENT
    Telegram user than the one this token was issued to."""
    token = await create_token(db_session, telegram_id=724233980, chat_id=724233980, phone="+1")
    await db_session.commit()
    async with await _client_and_app(db_session) as ac:
        r = await ac.post(
            f"/connect/{token.token}/session",
            json={"initData": _init_data(999999999), "dcId": 2, "authKey": _fake_auth_key()},
        )
        assert r.status_code == 401
        assert "does not match" in r.json()["detail"]


async def test_session_post_rejects_unknown_token(db_session):
    async with await _client_and_app(db_session) as ac:
        r = await ac.post(
            "/connect/does-not-exist/session",
            json={"initData": _init_data(724233980), "dcId": 2, "authKey": _fake_auth_key()},
        )
        assert r.status_code == 410


async def test_session_post_rejects_bad_auth_key(db_session):
    token = await create_token(db_session, telegram_id=724233980, chat_id=724233980, phone="+1")
    await db_session.commit()
    async with await _client_and_app(db_session) as ac:
        r = await ac.post(
            f"/connect/{token.token}/session",
            json={"initData": _init_data(724233980), "dcId": 2, "authKey": "not-base64!!!"},
        )
        assert r.status_code == 400
        assert "conversion failed" in r.json()["detail"]


async def test_session_check_failure_leaves_the_token_alive_for_a_retry(db_session):
    """A dead/unreachable session on the server-side check must not burn the
    user's one shot — they should be able to try again within their 15 min."""
    async def failing_checker(session_string: str) -> dict:
        raise RuntimeError("boom")

    token = await create_token(db_session, telegram_id=724233980, chat_id=724233980, phone="+1")
    await db_session.commit()
    async with await _client_and_app(db_session, session_checker=failing_checker) as ac:
        r = await ac.post(
            f"/connect/{token.token}/session",
            json={"initData": _init_data(724233980), "dcId": 2, "authKey": _fake_auth_key()},
        )
        assert r.status_code == 400
        # token was NOT consumed — a second, working attempt should succeed
        r2 = await ac.get(f"/connect/{token.token}")
        assert 'data-expired="false"' in r2.text


async def test_happy_path_saves_a_real_session(db_session):
    token = await create_token(db_session, telegram_id=724233980, chat_id=724233980, phone="+380671234567")
    await db_session.commit()
    async with await _client_and_app(db_session) as ac:
        r = await ac.post(
            f"/connect/{token.token}/session",
            json={"initData": _init_data(724233980), "dcId": 2, "authKey": _fake_auth_key()},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["account"]["username"] == "fixerhack"

    # the real storage.save_session was called — a Session row exists
    from sqlalchemy import select

    row = (await db_session.execute(select(Session).where(Session.phone_number == "+380671234567"))).scalar_one()
    assert row.is_active is True
    assert row.encrypted_session  # Fernet-encrypted, not plaintext


async def test_double_post_only_saves_once(db_session):
    """Second POST with the same (now-consumed) token must be refused, not
    silently save a second session."""
    token = await create_token(db_session, telegram_id=724233980, chat_id=724233980, phone="+1")
    await db_session.commit()
    async with await _client_and_app(db_session) as ac:
        payload = {"initData": _init_data(724233980), "dcId": 2, "authKey": _fake_auth_key()}
        first = await ac.post(f"/connect/{token.token}/session", json=payload)
        second = await ac.post(f"/connect/{token.token}/session", json=payload)
        assert first.status_code == 200
        assert second.status_code == 410


async def test_expired_token_is_rejected_even_with_perfect_payload(db_session):
    from datetime import datetime, timedelta

    token = await create_token(
        db_session, telegram_id=724233980, chat_id=724233980, phone="+1",
        now=datetime(2020, 1, 1),
    )
    await db_session.commit()
    async with await _client_and_app(db_session) as ac:
        r = await ac.post(
            f"/connect/{token.token}/session",
            json={"initData": _init_data(724233980), "dcId": 2, "authKey": _fake_auth_key()},
        )
        assert r.status_code == 410


# --- localization ------------------------------------------------------------


async def _page_html(db_session, *, telegram_id=8001, language_code=None):
    from management_bot.storage import upsert_user

    await upsert_user(db_session, telegram_id, language_code=language_code)
    token = await create_token(
        db_session, telegram_id=telegram_id, chat_id=telegram_id, phone="+15551234567"
    )
    await db_session.commit()
    async with await _client_and_app(db_session) as client:
        resp = await client.get(f"/connect/{token.token}")
    assert resp.status_code == 200
    return resp.text


async def test_page_leaves_no_untranslated_placeholder(db_session):
    """The failure this guards against is uniquely bad: a missed placeholder
    renders as literal __WA_SOMETHING__ on a page a paying client is looking
    at, and nothing else would catch it."""
    html = await _page_html(db_session)
    assert "__WA_" not in html
    assert "__TOKEN__" not in html and "__PHONE__" not in html
    assert "__EXPIRES_AT__" not in html and "__TTL_SECONDS__" not in html


async def test_expired_page_is_localized_too(db_session):
    """The expired page has no token row to look a language up by, but it must
    still be fully rendered rather than half-translated."""
    async with await _client_and_app(db_session) as client:
        resp = await client.get("/connect/no-such-token")
    assert resp.status_code == 200
    assert "__WA_" not in resp.text


async def test_page_follows_the_owners_language(db_session):
    uk = await _page_html(db_session, telegram_id=8101, language_code="uk")
    ru = await _page_html(db_session, telegram_id=8102, language_code="ru")
    en = await _page_html(db_session, telegram_id=8103, language_code="en")

    assert 'lang="uk"' in uk and "Введіть код" in uk
    assert 'lang="ru"' in ru and "Введите код" in ru
    assert 'lang="en"' in en and "Enter the code" in en


async def test_runtime_placeholders_survive_for_the_browser(db_session):
    """{phone}/{error} must reach the page UNformatted — app.js fills them in
    at runtime; formatting them server-side would strip the slots."""
    html = await _page_html(db_session, language_code="en")
    assert "{phone}" in html
    assert "{error}" in html


async def test_attribute_strings_cannot_break_out_of_their_attribute(db_session):
    """Localized strings land in data-* attributes quoted with " — an
    unescaped quote in any translation would end the attribute early and
    corrupt the markup."""
    html = await _page_html(db_session, language_code="en")
    start = html.index('id="connect-data"')
    block = html[start:html.index(">", start)]
    # every data-s-* value must be a properly closed, quoted attribute
    import re

    for name, value in re.findall(r'(data-s-[a-z0-9-]+)="([^"]*)"', block):
        assert '"' not in value, name


async def test_every_element_the_script_touches_exists(db_session):
    """Regression cover for a bug introduced while localizing: the template's
    <b id="phone-display"> was folded into the localized subtitle, but app.js
    still did getElementById("phone-display").textContent = ... — which throws
    at module scope and kills the ENTIRE login flow before it starts. Nothing
    server-side would have caught it."""
    import re
    from pathlib import Path

    js = (Path("connect_web") / "static" / "app.js").read_text(encoding="utf-8")
    html = await _page_html(db_session)

    referenced = set(re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', js))
    present = set(re.findall(r'id="([^"]+)"', html))
    missing = sorted(referenced - present)
    assert missing == [], f"app.js references element ids that the page doesn't define: {missing}"
