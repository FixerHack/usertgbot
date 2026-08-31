"""GramJS session components -> Telethon StringSession.

The two libraries' `StringSession` formats are NOT interchangeable: GramJS
packs (dcId, addressLength, address, port, authKey) and base64s it, Telethon
packs (dc_id, packed_ip, port, auth_key) via `struct` and urlsafe-base64s it.
Feeding one to the other silently fails. So the browser sends the two things
that actually matter — the DC id and the 256-byte auth key — and we rebuild a
Telethon session here through Telethon's own API rather than hand-packing
bytes (less to get subtly wrong, and it stays correct if Telethon ever
changes its encoding).

GramJS stores the DC as a hostname (`venus.web.telegram.org`, the WebSocket
gateway) but Telethon wants a packed IP, so we map dc_id -> the standard
production IP. These are Telegram's public, long-stable DC addresses.
"""

from __future__ import annotations

import base64

from telethon.crypto import AuthKey
from telethon.sessions import StringSession

_DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}
_PORT = 443
_AUTH_KEY_BYTES = 256


def gramjs_to_telethon(dc_id: int, auth_key_b64: str) -> str:
    """Build a Telethon StringSession from what the browser exported."""
    try:
        auth_key = base64.b64decode(auth_key_b64, validate=True)
    except Exception as exc:  # noqa: BLE001 - want the reason in the API response
        raise ValueError(f"authKey is not valid base64: {exc}") from exc

    if len(auth_key) != _AUTH_KEY_BYTES:
        raise ValueError(f"authKey must be {_AUTH_KEY_BYTES} bytes, got {len(auth_key)}")

    ip = _DC_IPS.get(dc_id)
    if ip is None:
        raise ValueError(f"unknown dc_id {dc_id!r} (known: {sorted(_DC_IPS)})")

    session = StringSession()
    session.set_dc(dc_id, ip, _PORT)
    session.auth_key = AuthKey(data=auth_key)
    return session.save()
