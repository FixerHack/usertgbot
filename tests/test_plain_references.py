"""Names that go somewhere HTML is not read.

`entities.ref` builds an `<a href="tg://user?id=…">` link for anyone without a
username. That is right for the manager bot, which sends HTML, and wrong in the
two places found live: an in-chat notice (Telethon posts markdown, so the tag
arrived as visible text in the client's own chat) and a .txt transcript, which
has no markup at all. It only ever showed on people without a username — a
`@handle` needs no tag — which is why it survived every earlier test.
"""

from __future__ import annotations

from types import SimpleNamespace

from userbot import entities


class _Client:
    """Stands in for Telethon's `get_entity`."""

    def __init__(self, entity=None, fail: bool = False):
        self._entity = entity
        self._fail = fail

    async def get_entity(self, _id):
        if self._fail:
            raise ValueError("no access to that entity")
        return self._entity


async def test_a_person_without_a_username_is_named_not_linked():
    """The live bug: `.mute` on someone with no @handle put raw HTML in the
    chat we were standing in."""
    client = _Client(SimpleNamespace(id=8885996156, username=None, first_name="User Agent", last_name="Manager"))

    got = await entities.plain_ref(client, 8885996156)

    assert got == "User Agent Manager"
    assert "<" not in got and "href" not in got


async def test_a_username_is_still_preferred():
    client = _Client(SimpleNamespace(id=1, username="mryoucode", first_name="Somebody", last_name=None))

    assert await entities.plain_ref(client, 1) == "@mryoucode"


async def test_an_unreachable_entity_falls_back_to_the_id():
    """Better a number than an empty space where a name belongs."""
    client = _Client(fail=True)

    assert await entities.plain_ref(client, 4242) == "4242"


async def test_the_html_reference_still_links():
    """The manager bot's messages are HTML and should keep the link."""
    client = _Client(SimpleNamespace(id=7, username=None, first_name="Ann", last_name=None))

    assert await entities.ref(client, 7) == '<a href="tg://user?id=7">Ann</a>'
