"""Shared settings schema: parsing + autoresponder decision."""

from datetime import datetime

from shared.settings_schema import (
    Autoresponder,
    MeCard,
    autoresponder_should_fire,
    parse_buttons,
    parse_hhmm,
)


def test_parse_buttons():
    raw = "Instagram | https://instagram.com/x\nbad line\nSite | https://a.com\nno url | ftp://x"
    buttons = parse_buttons(raw)
    assert [b.text for b in buttons] == ["Instagram", "Site"]


def test_parse_buttons_caps_at_max():
    raw = "\n".join(f"B{i} | https://a.com/{i}" for i in range(10))
    assert len(parse_buttons(raw)) == 5


def test_parse_hhmm():
    assert parse_hhmm("9:5") == "09:05"
    assert parse_hhmm("23:59") == "23:59"
    assert parse_hhmm("24:00") is None
    assert parse_hhmm("") is None
    assert parse_hhmm("nope") is None


def test_me_card_roundtrip():
    card = MeCard(text="hi", link="https://x", has_photo=True)
    restored = MeCard.from_dict(card.to_dict())
    assert restored == card


def test_autoresponder_disabled():
    ar = Autoresponder(enabled=False, message="hi")
    assert not autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 12, 0), sender_id=1, is_private=True)


def test_autoresponder_requires_private():
    ar = Autoresponder(enabled=True, message="hi")
    assert not autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 12, 0), sender_id=1, is_private=False)


def test_autoresponder_exceptions():
    ar = Autoresponder(enabled=True, message="hi", exceptions=[42])
    assert not autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 12, 0), sender_id=42, is_private=True)
    assert autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 12, 0), sender_id=7, is_private=True)


def test_autoresponder_time_window():
    ar = Autoresponder(enabled=True, message="hi", active_from="09:00", active_to="18:00")
    assert autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 10, 0), sender_id=1, is_private=True)
    assert not autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 20, 0), sender_id=1, is_private=True)


def test_autoresponder_window_wraps_midnight():
    ar = Autoresponder(enabled=True, message="hi", active_from="22:00", active_to="07:00")
    assert autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 23, 0), sender_id=1, is_private=True)
    assert autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 3, 0), sender_id=1, is_private=True)
    assert not autoresponder_should_fire(ar, now=datetime(2026, 1, 1, 12, 0), sender_id=1, is_private=True)
