"""Resolve a Telegram id/entity to a clickable reference for notifications."""

from __future__ import annotations

from userbot import formatting


def _name(entity) -> str | None:
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    return " ".join(p for p in parts if p) or getattr(entity, "title", None)


async def resolve(client, entity_or_id, lang: str = "uk") -> tuple[str, bool]:
    """Return (clickable reference, is_bot). Bot check lets callers skip the
    platform's own bots so the userbot never reacts to itself."""
    try:
        entity = await client.get_entity(entity_or_id)
    except Exception:
        return (
            formatting.entity_ref(entity_or_id if isinstance(entity_or_id, int) else None, lang=lang),
            False,
        )
    ref_str = formatting.entity_ref(
        getattr(entity, "id", None), getattr(entity, "username", None), _name(entity), lang=lang
    )
    return ref_str, bool(getattr(entity, "bot", False))


async def ref(client, entity_or_id, lang: str = "uk") -> str:
    ref_str, _ = await resolve(client, entity_or_id, lang)
    return ref_str


async def plain_ref(client, entity_or_id, lang: str = "uk") -> str:
    """The same reference as `ref`, with no markup in it.

    `ref` builds an HTML link for anyone without a username, which is right for
    the manager bot (aiogram, HTML) and wrong everywhere else: Telethon posts
    markdown, so the tag arrives as visible text in the very chat we are
    standing in, and a .txt transcript has no markup to speak of at all. It only
    ever showed on people without a username, since a `@handle` needs no tag.

    A link is no loss in either place. In-chat notices name the person already
    in front of you, and a transcript is read as text.
    """
    try:
        entity = await client.get_entity(entity_or_id)
    except Exception:
        entity = None
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    name = _name(entity) if entity is not None else None
    if name:
        return name
    if isinstance(entity_or_id, int):
        return str(entity_or_id)
    return formatting._t("unknown", lang)


async def plain_name(client, entity_or_id) -> str | None:
    """Plain (non-HTML) display name/title, for storage rather than notices —
    e.g. the ignored-chats list needs a title, not a clickable `<a>` link."""
    try:
        entity = await client.get_entity(entity_or_id)
    except Exception:
        return None
    name = _name(entity)
    username = getattr(entity, "username", None)
    return name or (f"@{username}" if username else None)
