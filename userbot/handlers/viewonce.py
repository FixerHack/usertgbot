"""Capture one-time (view-once) media before it self-destructs.

When an incoming photo/voice/video carries a self-destruct ttl, download it
immediately and forward it to the owner via the manager bot — otherwise it's
gone once opened.
"""

from __future__ import annotations

import logging

from telethon import TelegramClient, events

from db.queries import get_active_subscription_for_user
from db.session import get_session
from shared.settings_schema import Features
from userbot import entities, formatting
from userbot.context import WorkerContext
from userbot.detect import is_view_once, view_once_kind
from userbot.notify import notify_owner
from userbot.storage import load_features

logger = logging.getLogger(__name__)


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(incoming=True))
    async def capture(event: events.NewMessage.Event) -> None:
        message = event.message
        ttl = getattr(getattr(message, "media", None), "ttl_seconds", None)
        if not is_view_once(message.media is not None, ttl):
            return
        kind = view_once_kind(
            is_photo=bool(getattr(message, "photo", None)),
            is_voice=bool(getattr(message, "voice", None)),
            is_video=bool(getattr(message, "video_note", None) or getattr(message, "video", None)),
        )
        async with get_session() as db:
            if await get_active_subscription_for_user(db, ctx.owner_user_id) is None:
                return
            features = Features.from_dict(await load_features(db, ctx.owner_user_id))
            if kind == "photo" and not features.viewonce_photo:
                return
            if kind == "voice" and not features.viewonce_voice:
                return
        try:
            await _forward(client, event, ctx, kind)
        except Exception:
            logger.exception("failed to capture view-once media")


async def _forward(client: TelegramClient, event: events.NewMessage.Event, ctx: WorkerContext, kind: str) -> None:
    message = event.message
    sender_ref = await entities.ref(client, event.sender_id, ctx.owner_lang) if event.sender_id else "—"
    caption = formatting.format_view_once_notice(sender_ref, kind, ctx.owner_lang)

    data = await client.download_media(message, file=bytes)
    if not data:
        await notify_owner(ctx, caption)
        return
    if kind == "voice":
        await notify_owner(ctx, caption, voice=data)
    elif kind == "photo":
        await notify_owner(ctx, caption, photo=data)
    else:
        # fall back to a plain note for video/other — still better than losing it
        await notify_owner(ctx, caption)
