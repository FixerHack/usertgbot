"""Pure regex/parsing checks for owner-typed userbot commands that don't need
a live Telethon client to exercise (see gating tests for the tariff side)."""

from userbot.handlers.commands import SEND_RE


def test_send_re_matches_count_and_text():
    m = SEND_RE.match(".send 10 hello there")
    assert m is not None
    assert m.group(1) == "10"
    assert m.group(2) == "hello there"


def test_send_re_allows_multiline_text():
    m = SEND_RE.match(".send 3 line one\nline two")
    assert m is not None
    assert m.group(2) == "line one\nline two"


def test_send_re_rejects_missing_text():
    assert SEND_RE.match(".send 10") is None


def test_send_re_rejects_missing_count():
    assert SEND_RE.match(".send hello") is None


def test_send_re_ignores_unrelated_commands():
    assert SEND_RE.match(".sender 10 hi") is None
    assert SEND_RE.match(".check") is None
