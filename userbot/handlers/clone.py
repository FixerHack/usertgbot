"""`.clone` / `.stopc` — echo someone's messages back at them (Premium).

`.clone` starts repeating what one person says in one chat; `.stopc` stops.
The message is re-sent rather than forwarded, so it reads as if the owner
said it — a forward would carry the original author's name and defeat the
whole joke.

Two collisions worth knowing about, both settled deliberately:

* Muted beats cloned. Muting says "I do not want to see this"; cloning says
  "say it again, louder". Repeating a message we are about to delete would be
  absurd, so a muted target is never echoed.
* Telegram rate-limits. Echoing doubles the traffic in a chat, so a flood wait
  stops the clone outright instead of queueing behind it — the owner is told,
  and can start it again.
"""

from __future__ import annotations

import logging
import re

from telethon import TelegramClient, errors, events

from db.queries import clear_clone, get_clones, set_clone
from db.session import get_session
from shared.i18n import t
from userbot import entities
from userbot.context import WorkerContext
from userbot.handlers.mute import is_muted

logger = logging.getLogger(__name__)

CLONE_RE = re.compile(r"^\.clone\s*$")
STOPC_RE = re.compile(r"^\.stopc\s*$")


async def load_clones(ctx: WorkerContext) -> None:
    """`.clone` says "until .stopc", so it has to outlive a restart."""
    async with get_session() as db:
        rows = await get_clones(db, ctx.owner_user_id)
    ctx.cloned = {(row.chat_id, row.target_user_id) for row in rows}
    if ctx.cloned:
        logger.info("resumed %s clone(s) for owner_user_id=%s", len(ctx.cloned), ctx.owner_user_id)


async def _target_of(event) -> int | None:
    """Same rule as .mute: the person replied to, or the other side of a
    private chat. In a group with no reply there is nobody to infer."""
    if event.is_reply:
        replied = await event.get_reply_message()
        return getattr(replied, "sender_id", None) if replied else None
    if event.is_private:
        return event.chat_id
    return None


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=CLONE_RE))
    async def handle_clone(event: events.NewMessage.Event) -> None:
        from userbot.handlers.commands import _feature_enabled, _gate, _notice, clear_command

        if not await _feature_enabled(ctx, "clone"):
            await clear_command(event)
            return
        if await _gate(event, ctx, "clone") is None:
            return
        await clear_command(event)

        target = await _target_of(event)
        if target is None:
            await _notice(client, event.chat_id, t(ctx.owner_lang, "ub_clone_no_target"))
            return

        async with get_session() as db:
            await set_clone(
                db, owner_user_id=ctx.owner_user_id, chat_id=event.chat_id, target_user_id=target
            )
            await db.commit()
        ctx.cloned.add((event.chat_id, target))

        who = await entities.ref(client, target, ctx.owner_lang)
        await event.respond(t(ctx.owner_lang, "ub_clone_started", user=who))

    @client.on(events.NewMessage(outgoing=True, pattern=STOPC_RE))
    async def handle_stopc(event: events.NewMessage.Event) -> None:
        from userbot.handlers.commands import _feature_enabled, _gate, _notice, clear_command

        if not await _feature_enabled(ctx, "clone"):
            await clear_command(event)
            return
        if await _gate(event, ctx, "stopc") is None:
            return
        await clear_command(event)

        target = await _target_of(event)
        if target is None:
            await _notice(client, event.chat_id, t(ctx.owner_lang, "ub_clone_no_target"))
            return

        async with get_session() as db:
            removed = await clear_clone(
                db, owner_user_id=ctx.owner_user_id, chat_id=event.chat_id, target_user_id=target
            )
            await db.commit()
        ctx.cloned.discard((event.chat_id, target))

        if not removed:
            await _notice(client, event.chat_id, t(ctx.owner_lang, "ub_clone_not_running"))
            return
        who = await entities.ref(client, target, ctx.owner_lang)
        await event.respond(t(ctx.owner_lang, "ub_clone_stopped", user=who))

    @client.on(events.NewMessage(incoming=True))
    async def echo(event: events.NewMessage.Event) -> None:
        if not event.sender_id or (event.chat_id, event.sender_id) not in ctx.cloned:
            return
        # Muted wins: repeating a message we are about to delete makes no sense.
        if is_muted(ctx, event.chat_id, event.sender_id):
            return
        try:
            # Sent, not forwarded: a forward would carry their name on it.
            await client.send_message(event.chat_id, event.message)
        except errors.FloodWaitError:
            # Echoing doubles a chat's traffic, so this is the first thing to
            # hit a limit. Stop rather than queue behind it.
            logger.warning("clone stopped by FloodWaitError in chat_id=%s", event.chat_id)
            ctx.cloned.discard((event.chat_id, event.sender_id))
            async with get_session() as db:
                await clear_clone(
                    db, owner_user_id=ctx.owner_user_id,
                    chat_id=event.chat_id, target_user_id=event.sender_id,
                )
                await db.commit()
            await client.send_message(event.chat_id, t(ctx.owner_lang, "ub_clone_flood_stopped"))
        except Exception:
            logger.exception("clone: could not echo a message in chat_id=%s", event.chat_id)
