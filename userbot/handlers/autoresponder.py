"""Pro-tier autoresponder: auto-reply to incoming DMs per the owner's config.

Gated on the Pro tariff; obeys the configured time window and per-user
exceptions (shared.settings_schema), plus an in-memory per-sender cooldown so
one person doesn't trigger a flood of replies.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from telethon import TelegramClient, events

from db.session import get_session
from shared.settings_schema import Autoresponder, autoresponder_should_fire
from shared.tariffs import tariff_grants_command
from userbot import formatting
from userbot.context import WorkerContext
from userbot.gating import check_command
from userbot.notify import notify_owner
from userbot.storage import load_autoresponder, load_media

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 3600  # at most one auto-reply per sender per hour


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(incoming=True))
    async def maybe_reply(event: events.NewMessage.Event) -> None:
        if not event.is_private:
            return
        sender_id = event.sender_id
        if sender_id is None:
            return
        if _on_cooldown(ctx, sender_id):
            return
        # never auto-reply to bots (incl. our own management/manager bots)
        sender = await event.get_sender()
        if sender is not None and getattr(sender, "bot", False):
            return
        try:
            await _respond(client, event, ctx, sender_id, sender)
        except Exception:
            logger.exception("autoresponder failed")


async def _respond(client, event, ctx: WorkerContext, sender_id: int, sender=None) -> None:
    async with get_session() as db:
        gate = await check_command(db, ctx.owner_user_id, "autoresponder")
        if not gate.allowed or not tariff_grants_command(gate.tariff, "autoresponder"):
            return
        config = await load_autoresponder(db, ctx.owner_user_id)
        ar = Autoresponder.from_dict(config)
        if not autoresponder_should_fire(ar, now=datetime.now(), sender_id=sender_id, is_private=True):
            return
        photo = await load_media(db, ctx.owner_user_id, "autoresponder") if ar.has_photo else None

    text = ar.message
    if ar.buttons:
        links = formatting.format_link_buttons(ar.buttons)
        text = f"{text}\n\n{links}" if text else links

    if photo:
        import io

        stream = io.BytesIO(photo)
        stream.name = "photo.jpg"
        await client.send_file(event.chat_id, file=stream, caption=text, parse_mode="html", force_document=False)
    else:
        await client.send_message(event.chat_id, text, parse_mode="html", link_preview=False)
    ctx.autoresponder_last[sender_id] = time.monotonic()

    # notify the owner that the autoresponder fired, and to whom
    name = " ".join(p for p in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if p)
    sender_ref = formatting.entity_ref(
        sender_id, getattr(sender, "username", None), name or None, lang=ctx.owner_lang
    )
    await notify_owner(ctx, formatting.format_autoresponder_notice(sender_ref, event.raw_text or "", ctx.owner_lang))


def _on_cooldown(ctx: WorkerContext, sender_id: int) -> bool:
    last = ctx.autoresponder_last.get(sender_id)
    return last is not None and (time.monotonic() - last) < COOLDOWN_SECONDS
