"""Mini App `initData` verification.

This is the entire authentication story for the browser-based login: it is
what turns "someone POSTed JSON at us" into "this specific Telegram user
opened the page". Every test here is an attack that must fail — a hole in
this file means an attacker attaches a session to somebody else's account.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from shared.webapp_auth import verify_init_data

BOT_TOKEN = "8123456:AAF-test-token-value"
OTHER_TOKEN = "9999999:BBB-someone-elses-bot"


def make_init_data(
    *, user_id: int = 724233980, username: str = "fixerhack",
    age_seconds: int = 0, token: str = BOT_TOKEN, drop_hash: bool = False,
    extra: dict | None = None,
) -> str:
    """Build a genuinely-signed initData, the way Telegram would."""
    fields = {
        "auth_date": str(int(time.time()) - age_seconds),
        "query_id": "AAHtest_query_id",
        "user": json.dumps(
            {"id": user_id, "username": username, "first_name": "Danya"},
            separators=(",", ":"),
        ),
    }
    if extra:
        fields.update(extra)
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not drop_hash:
        fields["hash"] = signature
    return urlencode(fields)


# --- the happy path ---------------------------------------------------------


def test_valid_init_data_identifies_the_user():
    user = verify_init_data(make_init_data(), BOT_TOKEN)
    assert user.telegram_id == 724233980
    assert user.username == "fixerhack"
    assert user.full_name == "Danya"


def test_extra_unknown_fields_are_still_accepted():
    # Telegram adds fields over time; a new one must not break verification
    # as long as it was part of what got signed.
    raw = make_init_data(extra={"chat_type": "sender", "start_param": "connect"})
    assert verify_init_data(raw, BOT_TOKEN).telegram_id == 724233980


# --- forgery ----------------------------------------------------------------


def test_rejects_signature_from_a_different_bot_token():
    # An attacker with their own bot cannot mint initData for our service.
    raw = make_init_data(token=OTHER_TOKEN)
    with pytest.raises(ValueError, match="signature"):
        verify_init_data(raw, BOT_TOKEN)


def test_rejects_tampered_user_id():
    """The attack that matters most: keep a real signature, swap the identity,
    and the session gets attached to someone else's account."""
    raw = make_init_data(user_id=724233980).replace("724233980", "111111111")
    with pytest.raises(ValueError, match="signature"):
        verify_init_data(raw, BOT_TOKEN)


def test_rejects_tampered_username():
    raw = make_init_data(username="fixerhack").replace("fixerhack", "attacker")
    with pytest.raises(ValueError, match="signature"):
        verify_init_data(raw, BOT_TOKEN)


def test_rejects_missing_hash():
    with pytest.raises(ValueError, match="no hash"):
        verify_init_data(make_init_data(drop_hash=True), BOT_TOKEN)


def test_rejects_empty_input():
    with pytest.raises(ValueError, match="empty"):
        verify_init_data("", BOT_TOKEN)


# --- replay -----------------------------------------------------------------


def test_rejects_stale_init_data():
    """A signature stays valid forever, so without a freshness window a single
    captured initData could be replayed indefinitely."""
    with pytest.raises(ValueError, match="stale"):
        verify_init_data(make_init_data(age_seconds=3600), BOT_TOKEN)


def test_accepts_within_the_freshness_window():
    assert verify_init_data(make_init_data(age_seconds=60), BOT_TOKEN).telegram_id


def test_freshness_window_is_configurable():
    raw = make_init_data(age_seconds=600)
    with pytest.raises(ValueError, match="stale"):
        verify_init_data(raw, BOT_TOKEN, max_age_seconds=300)
    assert verify_init_data(raw, BOT_TOKEN, max_age_seconds=900).telegram_id


def test_rejects_missing_auth_date():
    fields = {"user": json.dumps({"id": 1}), "query_id": "x"}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="auth_date"):
        verify_init_data(urlencode(fields), BOT_TOKEN)


# --- malformed payloads -----------------------------------------------------


def test_rejects_user_that_is_not_json():
    fields = {"auth_date": str(int(time.time())), "user": "not-json"}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="JSON"):
        verify_init_data(urlencode(fields), BOT_TOKEN)


def test_rejects_user_without_an_integer_id():
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": "abc"})}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="integer id"):
        verify_init_data(urlencode(fields), BOT_TOKEN)
