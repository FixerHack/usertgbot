"""Telegram Mini App `initData` verification (HMAC-SHA256 on the bot token).

The only thing standing between "a Telegram user opened our page" and "anyone
posted JSON at our endpoint", so it fails closed on every anomaly.

This is the part that makes a Mini App meaningfully safer than a plain URL
button: Telegram signs the payload with a key derived from OUR bot token, so
a valid signature is cryptographic proof of which Telegram user opened the
page. A forwarded link is useless to anyone else — they cannot forge the
signature without the bot token.

Spec: secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token), then
hash = HMAC_SHA256(key=secret_key, msg=data_check_string), where
data_check_string is every field except `hash`, as `k=v` lines sorted by key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class WebAppUser:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None

    @property
    def full_name(self) -> str | None:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or None


def verify_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 300) -> WebAppUser:
    """Validate the signature and freshness, returning the authenticated user.

    Raises ValueError on anything suspicious — the caller must treat that as
    a hard refusal, never as "fall back to trusting the client".
    """
    if not init_data:
        raise ValueError("initData is empty")

    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise ValueError("initData has no hash field")

    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    # constant-time: a fast reject would leak how much of the hash matched
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("initData signature does not match")

    # Freshness: a valid signature stays valid forever, so without this an old
    # captured initData could be replayed indefinitely.
    auth_date = fields.get("auth_date")
    if not auth_date or not auth_date.isdigit():
        raise ValueError("initData has no usable auth_date")
    age = time.time() - int(auth_date)
    if age > max_age_seconds:
        raise ValueError(f"initData is stale ({int(age)}s old, max {max_age_seconds}s)")

    raw_user = fields.get("user")
    if not raw_user:
        raise ValueError("initData has no user field")
    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise ValueError(f"initData user is not valid JSON: {exc}") from exc

    telegram_id = user.get("id")
    if not isinstance(telegram_id, int):
        raise ValueError("initData user has no integer id")

    return WebAppUser(
        telegram_id=telegram_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
    )
