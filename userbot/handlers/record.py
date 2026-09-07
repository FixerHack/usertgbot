"""`.save` / `.unsave` — transcribe a chat to a text file (Premium).

`.save` starts recording the chat it is typed in — private or group — and
`.unsave` stops and delivers the archive. What is stored is a transcript, not
a backup: text, and a label for anything that is not text. Keeping the media
would turn a chat archive into an unbounded pile of blobs, for a feature whose
whole output is a .txt.

Every word this feature says goes to the owner's chat with the manager bot and
clears itself shortly after. Announcing "recording started" in the chat being
recorded would tell the other person exactly what they are not meant to know —
which makes the obvious place to put that message the one place it cannot go.

Like the mute list, which chats are recording is cached on the worker context
— the listener runs on every message in every chat, and a query per message to
answer "are we recording this one" would be paid by every chat that is not.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from telethon import TelegramClient, events

from db.queries import (
    MAX_RECORDED_MESSAGES,
    add_recorded_message,
    count_recorded,
    get_active_recordings,
    get_recorded_messages,
    start_recording,
    stop_recording,
)
from db.session import get_session
from shared.i18n import t
from shared.transcript import build_archive
from userbot import entities, formatting
from userbot.context import WorkerContext
from userbot.notify import notify_owner, notify_owner_briefly

logger = logging.getLogger(__name__)

SAVE_RE = re.compile(r"^\.save\s*$")
UNSAVE_RE = re.compile(r"^\.unsave\s*$")

# Any dot-command is ours, not part of the conversation being transcribed.
_OWN_COMMAND = re.compile(r"^\.[a-z]+\b")


def _describe(event, lang: str) -> str:
    """Text if there is any, otherwise a word for what was sent."""
    text = (event.raw_text or "").strip()
    if text:
        return text
    if event.sticker:
        return formatting.kind_label("sticker", lang)
    if event.photo:
        return formatting.kind_label("photo", lang)
    if event.voice:
        return formatting.kind_label("voice", lang)
    if event.video or event.video_note:
        return formatting.kind_label("video", lang)
    if event.geo is not None:
        return formatting.kind_label("location", lang)
    if event.file is not None:
        return formatting.kind_label("document", lang)
    return formatting.kind_label("media", lang)


async def load_recordings(ctx: WorkerContext) -> None:
    """Unfinished recordings survive a restart; the cache does not. Without
    this a `.save` would stop collecting on the next deploy and the archive
    would have a hole in it that nothing announced."""
    async with get_session() as db:
        rows = await get_active_recordings(db, ctx.owner_user_id)
    ctx.recording = {row.chat_id: row.id for row in rows}
    if ctx.recording:
        logger.info("resumed %s chat recording(s) for owner_user_id=%s", len(ctx.recording), ctx.owner_user_id)


async def _tell_owner(client, ctx: WorkerContext, chat_id: int, text: str) -> None:
    """Say it in the owner's chat with the bot, and take it back down.

    Falls back to a deliberately vague in-chat note when the manager bot
    cannot reach them: they still learn something happened, and the person
    being recorded still learns nothing.
    """
    from userbot.handlers.commands import _notice

    if not await notify_owner_briefly(ctx, text):
        await _notice(client, chat_id, t(ctx.owner_lang, "rec_see_bot"))


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=SAVE_RE))
    async def handle_save(event: events.NewMessage.Event) -> None:
        from userbot.handlers.commands import _feature_enabled, _gate, clear_command

        if not await _feature_enabled(ctx, "record"):
            await clear_command(event)
            return
        if await _gate(event, ctx, "save") is None:
            return
        await clear_command(event)

        if event.chat_id in ctx.recording:
            await _tell_owner(client, ctx, event.chat_id, t(ctx.owner_lang, "rec_already"))
            return

        title = await entities.plain_name(client, event.chat_id)
        async with get_session() as db:
            row = await start_recording(
                db, owner_user_id=ctx.owner_user_id, chat_id=event.chat_id, chat_title=title
            )
            await db.commit()
            ctx.recording[event.chat_id] = row.id

        await _tell_owner(client, ctx, event.chat_id, t(ctx.owner_lang, "rec_started"))

    @client.on(events.NewMessage(outgoing=True, pattern=UNSAVE_RE))
    async def handle_unsave(event: events.NewMessage.Event) -> None:
        from userbot.handlers.commands import _feature_enabled, _gate, _notice, clear_command

        if not await _feature_enabled(ctx, "record"):
            await clear_command(event)
            return
        if await _gate(event, ctx, "unsave") is None:
            return
        await clear_command(event)

        recording_id = ctx.recording.pop(event.chat_id, None)
        if recording_id is None:
            await _tell_owner(client, ctx, event.chat_id, t(ctx.owner_lang, "rec_not_running"))
            return

        async with get_session() as db:
            await stop_recording(db, recording_id)
            rows = await get_recorded_messages(db, recording_id)
            await db.commit()

        title = await entities.ref(client, event.chat_id, ctx.owner_lang)
        archive = build_archive(title, rows, ctx.owner_lang)
        # Delivered through the manager bot, not into the chat being recorded:
        # dropping a transcript of a conversation into that same conversation
        # is the last thing anyone wants.
        delivered = await notify_owner(
            ctx,
            t(ctx.owner_lang, "rec_ready", chat=title, count=len(rows)),
            document=archive.encode("utf-8"),
            document_filename=f"chat-{event.chat_id}.txt",
        )
        if not delivered:
            # The archive itself could not be delivered, so there is no point
            # trying to say so through the same channel.
            await _notice(client, event.chat_id, t(ctx.owner_lang, "rec_see_bot"))

    @client.on(events.NewMessage())
    async def collect(event: events.NewMessage.Event) -> None:
        recording_id = ctx.recording.get(event.chat_id)
        if recording_id is None:
            return
        # Our own commands are instructions to the bot, not part of the
        # conversation — and most of them delete themselves anyway.
        if event.out and _OWN_COMMAND.match(event.raw_text or ""):
            return

        try:
            async with get_session() as db:
                if await count_recorded(db, recording_id) >= MAX_RECORDED_MESSAGES:
                    # Stop rather than grow: an archive nobody can open is
                    # not a better outcome than a shorter one that says why.
                    await stop_recording(db, recording_id)
                    await db.commit()
                    ctx.recording.pop(event.chat_id, None)
                    logger.warning("recording %s hit the message cap and was stopped", recording_id)
                    return

                sender = (
                    await entities.ref(client, event.sender_id, ctx.owner_lang) if event.sender_id else "—"
                )
                await add_recorded_message(
                    db,
                    recording_id=recording_id,
                    sent_at=event.message.date or datetime.now(timezone.utc),
                    sender=sender,
                    is_outgoing=bool(event.out),
                    text=_describe(event, ctx.owner_lang),
                )
                await db.commit()
        except Exception:
            # A recording that fails must not take the message with it.
            logger.exception("failed to record a message in chat_id=%s", event.chat_id)
