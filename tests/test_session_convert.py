"""GramJS session components -> Telethon StringSession.

A silent failure here means a login that looked perfect to the user produces
a session the worker cannot load, and nobody finds out until the bot is
mysteriously dead. So the round trip is checked against Telethon's own parser
rather than against our own idea of the format.
"""

from __future__ import annotations

import base64
import os

import pytest
from telethon.sessions import StringSession

from shared.session_convert import gramjs_to_telethon

VALID_KEY = os.urandom(256)
VALID_B64 = base64.b64encode(VALID_KEY).decode()


def test_round_trips_through_telethons_own_parser():
    parsed = StringSession(gramjs_to_telethon(2, VALID_B64))
    assert parsed.dc_id == 2
    assert parsed.port == 443
    assert parsed.auth_key.key == VALID_KEY, "the auth key must survive byte for byte"


@pytest.mark.parametrize("dc_id", [1, 2, 3, 4, 5])
def test_every_production_dc_maps_to_a_real_address(dc_id):
    """signIn migrates the account to its home DC — seen live going 4->2 and
    4->5 — so every DC has to convert, not just the one we happened to test."""
    parsed = StringSession(gramjs_to_telethon(dc_id, VALID_B64))
    assert parsed.dc_id == dc_id
    assert parsed.server_address.count(".") == 3, "must be a packable IPv4"


def test_different_dcs_produce_different_addresses():
    addrs = {StringSession(gramjs_to_telethon(dc, VALID_B64)).server_address for dc in range(1, 6)}
    assert len(addrs) == 5, "a copy-paste in the DC map would silently point at the wrong one"


def test_rejects_unknown_dc():
    with pytest.raises(ValueError, match="unknown dc_id"):
        gramjs_to_telethon(99, VALID_B64)


@pytest.mark.parametrize(
    "bad_key, reason",
    [
        (base64.b64encode(b"too short").decode(), "256 bytes"),
        (base64.b64encode(os.urandom(512)).decode(), "256 bytes"),
        ("!!!not base64!!!", "base64"),
        ("", "256 bytes"),
    ],
    ids=["short", "long", "not_base64", "empty"],
)
def test_rejects_malformed_auth_keys(bad_key, reason):
    """Fail loudly at conversion time — a malformed key that slips through
    becomes an unloadable session discovered much later, far from the cause."""
    with pytest.raises(ValueError, match=reason):
        gramjs_to_telethon(2, bad_key)


def test_output_is_a_valid_telethon_string_session():
    out = gramjs_to_telethon(2, VALID_B64)
    assert out.startswith("1"), "Telethon's StringSession version prefix"
    # the real acceptance test: Telethon parses it without raising
    StringSession(out)
