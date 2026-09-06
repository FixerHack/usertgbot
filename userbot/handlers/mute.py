"""`.mute` / `.unmute` — silence someone in one chat.

Two halves that have to agree with each other:

* the commands, which record who is silenced and for how long, and
* a listener that deletes anything a silenced person sends afterwards.

The listener is why the mute list is cached in the worker context rather than
read from the database: it runs on every incoming message, and a query per
message to answer "is this person muted" would be paid by everyone, muted or
not.

Deleting someone's message is possible in a private chat, where Telegram lets
either side revoke; in a group it needs admin rights and simply fails
otherwise. That failure is logged, not surfaced — a mute that cannot enforce
itself is still a record of intent, and shouting about it in the chat would
be worse than quiet.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, events

from db.queries import clear_mute, get_mutes, set_mute
from db.session import get_session
from shared.i18n import t
from userbot import entities
from userbot.context import WorkerContext

logger = logging.getLogger(__name__)

# `.mute` alone, or `.mute 180` for a number of minutes.
MUTE_RE = re.compile(r"^\.mute(?:\s+(\d{1,6}))?\s*$")
UNMUTE_RE = re.compile(r"^\.unmute\s*$")

# A mute is bounded by how long the deletion right lasts anyway; a year is
# past the point where "forever" is the honest word for it.
MAX_MUTE_MINUTES = 365 * 24 * 60



def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_muted(ctx: WorkerContext, chat_id: int, user_id: int, *, now: datetime | None = None) -> bool:
    """Cheap enough to call on every incoming message — a dict lookup."""
    key = (chat_id, user_id)
    if key not in ctx.muted:
        return False
    until = ctx.muted[key]
    if until is None:
        return True
    if until > (now or _now()):
        return True
    # Expired: drop it so the next message does not re-check the same clock.
    ctx.muted.pop(key, None)
    return False


async def load_mutes(ctx: WorkerContext) -> None:
    """Fill the cache at worker start. Rows outlive restarts; the cache does
    not, so without this a mute would quietly stop applying after a deploy."""
    async with get_session() as db:
        rows = await get_mutes(db, ctx.owner_user_id)
    ctx.muted = {
        (row.chat_id, row.target_user_id): (
            row.until.replace(tzinfo=timezone.utc) if row.until is not None else None
        )
        for row in rows
    }
    if ctx.muted:
        logger.info("loaded %s mute(s) for owner_user_id=%s", len(ctx.muted), ctx.owner_user_id)


async def _target_of(event) -> int | None:
    """Who the command is aimed at: the person replied to, or — in a private
    chat — simply the other side. In a group with no reply there is nobody to
    infer, and guessing would mute the wrong person."""
    if event.is_reply:
        replied = await event.get_reply_message()
        return getattr(replied, "sender_id", None) if replied else None
    if event.is_private:
        return event.chat_id
    return None


def _humanize(minutes: int, lang: str) -> str:
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(t(lang, "dur_hours", n=hours))
    if mins or not hours:
        parts.append(t(lang, "dur_minutes", n=mins))
    return " ".join(parts)


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=MUTE_RE))
    async def handle_mute(event: events.NewMessage.Event) -> None:
        from userbot.handlers.commands import _feature_enabled, _gate, _notice, clear_command

        if not await _feature_enabled(ctx, "mute"):
            await clear_command(event)
            return
        if await _gate(event, ctx, "mute") is None:
            return

        target = await _target_of(event)
        if target is None:
            await clear_command(event)
            # Self-deleting, like .send's limit notices: it is a correction
            # for the owner, not something the other side needs left in the
            # chat for good.
            await _notice(client, event.chat_id, t(ctx.owner_lang, "ub_mute_no_target"))
            return

        raw = event.pattern_match.group(1)
        minutes = min(int(raw), MAX_MUTE_MINUTES) if raw else 0
        until = _now() + timedelta(minutes=minutes) if minutes else None

        async with get_session() as db:
            await set_mute(
                db, owner_user_id=ctx.owner_user_id, chat_id=event.chat_id,
                target_user_id=target, until=until,
            )
            await db.commit()
        ctx.muted[(event.chat_id, target)] = until

        who = await entities.ref(client, target, ctx.owner_lang)
        text = (
            t(ctx.owner_lang, "ub_muted_for", user=who, duration=_humanize(minutes, ctx.owner_lang))
            if until is not None
            else t(ctx.owner_lang, "ub_muted_forever", user=who)
        )
        await event.respond(text)
        await clear_command(event)

    @client.on(events.NewMessage(outgoing=True, pattern=UNMUTE_RE))
    async def handle_unmute(event: events.NewMessage.Event) -> None:
        from userbot.handlers.commands import _feature_enabled, _gate, _notice, clear_command

        if not await _feature_enabled(ctx, "mute"):
            await clear_command(event)
            return
        if await _gate(event, ctx, "unmute") is None:
            return

        target = await _target_of(event)
        if target is None:
            await clear_command(event)
            await _notice(client, event.chat_id, t(ctx.owner_lang, "ub_mute_no_target"))
            return

        async with get_session() as db:
            removed = await clear_mute(
                db, owner_user_id=ctx.owner_user_id, chat_id=event.chat_id, target_user_id=target
            )
            await db.commit()
        ctx.muted.pop((event.chat_id, target), None)

        if not removed:
            await clear_command(event)
            await _notice(client, event.chat_id, t(ctx.owner_lang, "ub_not_muted"))
            return
        who = await entities.ref(client, target, ctx.owner_lang)
        await event.respond(t(ctx.owner_lang, "ub_unmuted", user=who))
        await clear_command(event)

    @client.on(events.NewMessage(incoming=True))
    async def drop_muted(event: events.NewMessage.Event) -> None:
        if not event.sender_id or not is_muted(ctx, event.chat_id, event.sender_id):
            return
        try:
            await event.delete()
        except Exception:
            # No right to delete here (a group where we are not admin). The
            # mute stays on record; there is nothing useful to say in-chat.
            logger.debug("mute: could not delete message in chat_id=%s", event.chat_id, exc_info=True)
