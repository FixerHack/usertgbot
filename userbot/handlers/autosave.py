"""Auto-save messages that others delete or edit (Standard tier).

Incoming messages are cached in memory; when a delete/edit event fires we
resolve the original content from the cache and persist it — but only if the
owner currently has an active subscription.

Photos/voice messages: unlike text, once Telegram tells us a message was
deleted there is no way to fetch its media back — the message is gone, so we
must download it eagerly on receipt (like the view-once capture does) and
hold the bytes in the cache until either the message is confirmed safe or
gets deleted. This used to be silently skipped entirely: `remember()` only
cached `raw_text`, which is empty for a caption-less photo/voice message, and
`on_deleted` dropped anything with no cached text — so deleting a bare photo
or voice message in a group was never caught (confirmed by a real tester
report). Bounded by `_MAX_MEDIA_BYTES` and gated on an active subscription
before downloading, so it doesn't burn bandwidth on chats belonging to
non-subscribers.
"""

from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from telethon import TelegramClient, events

from db.queries import get_active_subscription_for_user, is_chat_ignored, save_captured_message
from db.session import get_session
from shared.i18n import t
from shared.settings_schema import Features
from userbot import entities, formatting
from userbot.context import WorkerContext
from userbot.handlers.mute import is_muted
from userbot.notify import notify_owner
from userbot.storage import load_features

logger = logging.getLogger(__name__)

# Telegram caps callback_data at 64 bytes; leave room for "ignore_chat:" +
# the chat id before truncating the title (UTF-8 byte-aware, so Cyrillic
# titles don't overflow the limit).
_TITLE_BYTE_BUDGET = 28

# Memory safety net for eagerly-downloaded photo/voice — most are well under
# this; skip caching (not the message itself) for anything bigger instead of
# holding a huge blob in memory just in case it gets deleted.
_MAX_MEDIA_BYTES = 8 * 1024 * 1024


def _truncate_utf8(s: str, max_bytes: int) -> str:
    raw = s.encode("utf-8")
    if len(raw) <= max_bytes:
        return s
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _ignore_chat_kb(
    chat_id: int, chat_title: str | None, lang: str, *, also_delete: int | None = None
) -> InlineKeyboardMarkup:
    """`also_delete` is the id of a companion message posted alongside this
    one — a sticker, which cannot carry the notice as a caption. Deleting the
    notice has to take its file with it, or the button tidies half of what the
    owner is looking at."""
    safe_title = _truncate_utf8((chat_title or "").replace(":", " ").strip(), _TITLE_BYTE_BUDGET)
    delete_data = "notice:del" if also_delete is None else f"notice:del:{also_delete}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "btn_ignore_chat"), callback_data=f"ignore_chat:{chat_id}:{safe_title}"
                ),
                # Recovered messages pile up fast. Without this the only way
                # to clear one is Telegram's own delete menu, two taps away.
                InlineKeyboardButton(text=t(lang, "btn_delete_notice"), callback_data=delete_data),
            ]
        ]
    )


async def _capture_media(client, ctx, event) -> tuple[bytes | None, str | None, str | None]:
    """Eagerly download any downloadable media (photo/voice/video, and as a
    generic fallback: documents, GIFs, stickers, audio, ...) so it survives a
    later deletion — skipped if there's nothing to download, it's oversized,
    or the owner has no active subscription (avoids paying the download cost
    for good). Anything we can't classify into a specific kind is sent back
    as a plain document — loses native rendering (a GIF forwarded as a
    document doesn't autoplay, say) but still recovers the actual content,
    which is the point."""
    # Stickers first: a video sticker (.webm) also answers to `event.video`,
    # and classifying one as a video would send it back as a playable clip.
    if event.sticker:
        kind = "sticker"
    elif event.photo:
        kind = "photo"
    elif event.voice:
        kind = "voice"
    elif event.video or event.video_note:
        kind = "video"
    elif event.file is not None:
        kind = "document"  # documents, GIFs, audio, etc.
    else:
        return None, None, None

    size = getattr(event.file, "size", None) if event.file else None
    filename = getattr(event.file, "name", None) if event.file else None
    logger.debug(
        "MEDIA CAPTURE: chat_id=%s msg_id=%s kind=%s size=%s is_private=%s",
        event.chat_id, event.id, kind, size, event.is_private,
    )
    # Fail closed: an unknown size means we can't bound the download, so skip
    # rather than risk an unbounded fetch (defeats the whole point of the cap).
    if size is None or size > _MAX_MEDIA_BYTES:
        logger.debug("MEDIA CAPTURE: skipped (too large or unknown size, %s > %s)", size, _MAX_MEDIA_BYTES)
        return None, None, None

    async with get_session() as db:
        if await get_active_subscription_for_user(db, ctx.owner_user_id) is None:
            logger.debug("MEDIA CAPTURE: skipped (no active subscription for owner_user_id=%s)", ctx.owner_user_id)
            return None, None, None

    try:
        data = await client.download_media(event.message, file=bytes)
    except Exception:
        logger.exception("failed to eagerly download %s for anti-delete cache", kind)
        return None, None, None
    logger.debug(
        "MEDIA CAPTURE: downloaded %s bytes for chat_id=%s msg_id=%s", len(data) if data else 0, event.chat_id, event.id
    )
    return data, kind, filename


def _capture_location(event) -> tuple[float, float] | None:
    """Locations/venues aren't files — nothing to download, just the point
    itself (works for plain, live, and venue shares alike)."""
    geo = event.geo
    if geo is None or not hasattr(geo, "lat"):
        return None  # GeoPointEmpty (live location that expired) has no lat/long
    return (geo.lat, geo.long)


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(incoming=True))
    async def remember(event: events.NewMessage.Event) -> None:
        # Muting someone means not wanting to see what they write. Their
        # messages get deleted (by us), and anything remembered here would
        # come straight back as a "deleted message" notice — the opposite of
        # what was asked for. Never cached, so there is nothing to restore.
        if event.sender_id and is_muted(ctx, event.chat_id, event.sender_id):
            return
        media, media_kind, media_filename = await _capture_media(client, ctx, event)
        await ctx.cache.remember(
            chat_id=event.chat_id,
            message_id=event.id,
            sender_id=event.sender_id,
            text=event.raw_text,
            media=media,
            media_kind=media_kind,
            media_filename=media_filename,
            location=_capture_location(event),
        )

    @client.on(events.MessageDeleted())
    async def on_deleted(event: events.MessageDeleted.Event) -> None:
        logger.debug(
            "MEDIA CAPTURE: MessageDeleted event.chat_id=%s deleted_ids=%s", event.chat_id, list(event.deleted_ids)
        )
        for message_id in event.deleted_ids:
            # For DMs/small groups Telegram's delete update carries no peer at
            # all, so event.chat_id is None here — fall back to the reverse
            # index (see message_cache.py) instead of silently dropping it.
            cached = (
                await ctx.cache.pop(event.chat_id, message_id)
                if event.chat_id
                else await ctx.cache.pop_by_message_id(message_id)
            )
            logger.debug(
                "MEDIA CAPTURE: lookup msg_id=%s -> %s",
                message_id,
                None if cached is None else f"text={cached.text!r} media_kind={cached.media_kind} media_len={len(cached.media) if cached.media else 0} location={cached.location}",
            )
            if cached is None or (not cached.text and cached.media is None and cached.location is None):
                continue
            await _handle(
                client, ctx, cached.chat_id, message_id, cached.sender_id, "deleted", cached.text, None,
                media=cached.media, media_kind=cached.media_kind, media_filename=cached.media_filename,
                location=cached.location,
            )

    @client.on(events.MessageEdited(incoming=True))
    async def on_edited(event: events.MessageEdited.Event) -> None:
        cached = await ctx.cache.get(event.chat_id, event.id)
        previous = cached.text if cached else None
        # an edit only ever changes the caption/text, never the media/location
        # itself, so re-remembering here doesn't need to re-capture anything —
        # carry the already-cached values forward untouched
        await ctx.cache.remember(
            event.chat_id, event.id, event.sender_id, event.raw_text,
            media=cached.media if cached else None,
            media_kind=cached.media_kind if cached else None,
            media_filename=cached.media_filename if cached else None,
            location=cached.location if cached else None,
        )
        if previous is None or previous == event.raw_text:
            return
        await _handle(client, ctx, event.chat_id, event.id, event.sender_id, "edited", event.raw_text, previous)


async def _handle(
    client, ctx, chat_id, message_id, sender_id, event_type, text, previous_text,
    *,
    media: bytes | None = None,
    media_kind: str | None = None,
    media_filename: str | None = None,
    location: tuple[float, float] | None = None,
) -> None:
    # never react to bots — including our own management/manager bots
    sender_ref, is_bot = (
        (await entities.resolve(client, sender_id, ctx.owner_lang))
        if sender_id
        else (t(ctx.owner_lang, "unknown"), False)
    )
    if is_bot:
        return
    chat_ref = await entities.ref(client, chat_id, ctx.owner_lang)

    try:
        async with get_session() as db:
            if await get_active_subscription_for_user(db, ctx.owner_user_id) is None:
                logger.debug(
                    "MEDIA CAPTURE: _handle gated out (no active subscription) chat_id=%s msg_id=%s",
                    chat_id, message_id,
                )
                return
            features = Features.from_dict(await load_features(db, ctx.owner_user_id))
            if event_type == "deleted" and not features.deleted:
                logger.debug("MEDIA CAPTURE: _handle gated out (features.deleted off)")
                return
            if event_type == "edited" and not features.edited:
                logger.debug("MEDIA CAPTURE: _handle gated out (features.edited off)")
                return
            if await is_chat_ignored(db, ctx.owner_user_id, chat_id):
                logger.debug("MEDIA CAPTURE: _handle gated out (chat ignored) chat_id=%s", chat_id)
                return
            await save_captured_message(
                db,
                owner_user_id=ctx.owner_user_id,
                chat_id=chat_id,
                message_id=message_id,
                sender_id=sender_id,
                event_type=event_type,
                text=text,
                previous_text=previous_text,
            )
            await db.commit()
    except Exception:
        logger.exception("failed to auto-save %s message", event_type)
        return

    if event_type == "deleted":
        placeholder_kind = media_kind or ("location" if location else None)
        body = text or (formatting.kind_label(placeholder_kind, ctx.owner_lang) if placeholder_kind else "")
        notice = formatting.format_deleted_notice(chat_ref, sender_ref, body, ctx.owner_lang)
    else:
        notice = formatting.format_edited_notice(chat_ref, sender_ref, previous_text or "", text or "", ctx.owner_lang)
    chat_title = await entities.plain_name(client, chat_id)
    kb = _ignore_chat_kb(chat_id, chat_title, ctx.owner_lang)
    if media_kind == "photo":
        await notify_owner(ctx, notice, photo=media, reply_markup=kb)
    elif media_kind == "voice":
        await notify_owner(ctx, notice, voice=media, reply_markup=kb)
    elif media_kind == "video":
        await notify_owner(ctx, notice, video=media, reply_markup=kb)
    elif media_kind == "sticker":
        # Two messages, on purpose. Telegram turns a .webp/.tgs/.webm document
        # back into a sticker, and stickers cannot carry a caption — so the
        # "who deleted what" line was being dropped on the floor, leaving the
        # owner with a bare image and no idea who sent it.
        #
        # File first, notice second: that is the order captioned media already
        # reads in (picture above, words below), and it means the notice can
        # carry the file's id so one tap on 🗑 clears both. The other way round
        # left the sticker stranded once the notice was deleted.
        sent = await notify_owner(ctx, "", document=media, document_filename=media_filename or "sticker.webp")
        await notify_owner(
            ctx,
            notice,
            reply_markup=_ignore_chat_kb(
                chat_id, chat_title, ctx.owner_lang,
                also_delete=getattr(sent, "message_id", None),
            ),
        )
    elif media_kind == "document":
        await notify_owner(ctx, notice, document=media, document_filename=media_filename or "file", reply_markup=kb)
    elif location is not None:
        await notify_owner(ctx, notice, location=location, reply_markup=kb)
    else:
        await notify_owner(ctx, notice, reply_markup=kb)
