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


# --- URL validation ----------------------------------------------------------


def test_only_renderable_schemes_are_accepted():
    """Telegram renders only these as links. Anything else becomes a text
    entity carrying a junk URL, and the send fails — which for `.me` means it
    silently does nothing inside a chat with a client."""
    from shared.settings_schema import is_valid_url

    for good in ("http://x.com", "https://t.me/x", "tg://resolve?domain=x"):
        assert is_valid_url(good), good
    for bad in ("не посилання", "javascript:alert(1)", "ftp://x", "data:text/html,x", "", None):
        assert not is_valid_url(bad), bad


def test_buttons_and_the_card_link_share_one_rule():
    """They were validated differently once: buttons checked the scheme, the
    card link accepted anything."""
    from shared.settings_schema import is_valid_url, parse_buttons

    assert parse_buttons("Label | javascript:alert(1)") == []
    assert not is_valid_url("javascript:alert(1)")
    assert len(parse_buttons("Label | https://ok.test")) == 1
    assert is_valid_url("https://ok.test")


def test_a_settings_row_written_before_a_switch_existed_keeps_the_feature_on():
    """Features live in a JSON blob, so an old row simply lacks the new keys.
    Reading a missing key as False would silently switch off something the
    owner never touched."""
    from shared.settings_schema import Features

    old_row = {"deleted": False, "edited": True, "info": True, "me": True, "ban": True, "check": True}
    features = Features.from_dict(old_row)

    assert features.deleted is False, "what was stored is respected"
    assert features.viewonce_video is True
    assert features.send is True


def test_every_switch_survives_a_round_trip():
    from shared.settings_schema import Features

    off = Features(**{name: False for name in Features._FIELDS})
    assert Features.from_dict(off.to_dict()).to_dict() == off.to_dict()
