"""Owner-typed userbot commands: .info, .me, .ban, .check, .send.

All are `outgoing=True` — the account owner types them in any chat and the
userbot (running as that account) acts. Every command is gated on the owner's
active subscription/tariff.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from math import ceil

from telethon import TelegramClient, errors, events
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.functions.users import GetFullUserRequest

from db.queries import consume_check, mark_send_used, peek_send_allowance
from db.session import get_session
from shared.i18n import t
from shared.settings_schema import Features, MeCard
from shared.tariffs import get_plan
from userbot import formatting
from userbot.context import WorkerContext
from userbot.gating import check_command
from userbot.notify import notify_owner
from userbot.storage import load_features, load_media, load_me_card

logger = logging.getLogger(__name__)


def _photo_stream(data: bytes) -> io.BytesIO:
    """Named BytesIO so Telethon sends it as a photo, not a document."""
    stream = io.BytesIO(data)
    stream.name = "photo.jpg"
    return stream


INFO_RE = r"^\.info\s*$"
ME_RE = r"^\.me\s*$"
BAN_RE = r"^\.ban\s*$"
CHECK_RE = r"^\.check\b.*$"
# .send <count> <text> — text can span multiple lines, hence re.DOTALL. The
# actual max is tariff-dependent (TariffPlan.send_max_count); \d{1,3} just
# caps the regex itself well above the highest plan's limit.
SEND_RE = re.compile(r"^\.send\s+(\d{1,3})\s+(.+)$", re.DOTALL)


async def _feature_enabled(ctx: WorkerContext, name: str) -> bool:
    async with get_session() as db:
        return getattr(Features.from_dict(await load_features(db, ctx.owner_user_id)), name)


def register(client: TelegramClient, ctx: WorkerContext) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=INFO_RE))
    async def handle_info(event: events.NewMessage.Event) -> None:
        if not await _gate(event, ctx, "info"):
            return
        if not await _feature_enabled(ctx, "info"):
            await event.reply(t(ctx.owner_lang, "ub_feature_disabled"))
            return
        try:
            await _do_info(client, event, ctx)
        except Exception:
            logger.exception(".info failed")
            await event.reply(t(ctx.owner_lang, "ub_info_error"))

    @client.on(events.NewMessage(outgoing=True, pattern=ME_RE))
    async def handle_me(event: events.NewMessage.Event) -> None:
        if not await _gate(event, ctx, "me"):
            return
        if not await _feature_enabled(ctx, "me"):
            await event.reply(t(ctx.owner_lang, "ub_feature_disabled"))
            return
        try:
            await _do_me(client, event, ctx)
        except Exception:
            logger.exception(".me failed")

    @client.on(events.NewMessage(outgoing=True, pattern=BAN_RE))
    async def handle_ban(event: events.NewMessage.Event) -> None:
        if not await _gate(event, ctx, "ban"):
            return
        if not await _feature_enabled(ctx, "ban"):
            await event.reply(t(ctx.owner_lang, "ub_feature_disabled"))
            return
        try:
            await _do_ban(client, event)
        except Exception:
            logger.exception(".ban failed")
            await event.reply(t(ctx.owner_lang, "ub_ban_error"))

    @client.on(events.NewMessage(outgoing=True, pattern=CHECK_RE))
    async def handle_check(event: events.NewMessage.Event) -> None:
        gate = await _gate(event, ctx, "check")
        if not gate:
            return
        if not await _feature_enabled(ctx, "check"):
            await event.reply(t(ctx.owner_lang, "ub_feature_disabled"))
            return
        await _do_check(event, ctx, gate)

    @client.on(events.NewMessage(outgoing=True, pattern=SEND_RE))
    async def handle_send(event: events.NewMessage.Event) -> None:
        if not await _feature_enabled(ctx, "send"):
            await clear_command(event)
            return
        gate = await _gate(event, ctx, "send")
        if gate is None:
            return
        try:
            await _do_send(client, event, ctx, gate)
        except Exception:
            logger.exception(".send failed")


# --- command bodies --------------------------------------------------------


async def _do_info(client: TelegramClient, event: events.NewMessage.Event, ctx: WorkerContext) -> None:
    entity = await event.get_chat()
    if event.is_private:
        bio = None
        try:
            full = await client(GetFullUserRequest(entity))
            bio = full.full_user.about
        except Exception:
            logger.debug("GetFullUserRequest failed", exc_info=True)
        info = formatting.TargetInfo(
            entity_id=getattr(entity, "id", 0),
            is_chat=False,
            title_or_name=_full_name(entity, ctx.owner_lang),
            username=getattr(entity, "username", None),
            is_premium=bool(getattr(entity, "premium", False)),
            is_verified=bool(getattr(entity, "verified", False)),
            is_bot=bool(getattr(entity, "bot", False)),
            phone=getattr(entity, "phone", None),
            bio=bio,
        )
    else:
        info = formatting.TargetInfo(
            entity_id=getattr(entity, "id", 0),
            is_chat=True,
            title_or_name=getattr(entity, "title", None) or t(ctx.owner_lang, "ub_entity_chat"),
            username=getattr(entity, "username", None),
            members_count=getattr(entity, "participants_count", None),
        )
    text = formatting.format_target_info(info, ctx.owner_lang)

    # Exactly one avatar (the current profile photo), never the whole album.
    photo = await client.download_profile_photo(entity, file=bytes)
    await event.delete()

    # Deliver privately via the manager bot; fall back to posting in-chat.
    if await notify_owner(ctx, text, photo=photo):
        return
    if photo:
        await client.send_file(
            event.chat_id, file=_photo_stream(photo), caption=text, parse_mode="html", force_document=False
        )
    else:
        await client.send_message(event.chat_id, text, parse_mode="html", link_preview=False)


async def _do_me(client: TelegramClient, event: events.NewMessage.Event, ctx: WorkerContext) -> None:
    async with get_session() as db:
        card_dict = await load_me_card(db, ctx.owner_user_id)
        photo = await load_media(db, ctx.owner_user_id, "me") if card_dict else None
    card = MeCard.from_dict(card_dict)
    if card.is_empty():
        await event.reply(t(ctx.owner_lang, "ub_me_not_set"))
        return

    text, buttons = formatting.build_me_card(card)
    if buttons:
        links = formatting.format_link_buttons(buttons)
        text = f"{text}\n\n{links}" if text else links

    await event.delete()
    if photo:
        await client.send_file(
            event.chat_id, file=_photo_stream(photo), caption=text, parse_mode="html", force_document=False
        )
    else:
        await client.send_message(event.chat_id, text, parse_mode="html", link_preview=False)


async def _do_ban(client: TelegramClient, event: events.NewMessage.Event) -> None:
    """DM: block the other party and wipe the conversation on both sides.
    Group: if `.ban` is a reply to someone, actually remove+ban them from the
    group (requires the account to have ban rights there); with no reply
    target (or no rights), just leave — there's nobody specific to act on."""
    reply = await event.get_reply_message()
    target = await reply.get_sender() if reply is not None else None

    if event.is_private:
        peer = target or await event.get_chat()
        await client(BlockRequest(id=peer))
        await client.delete_dialog(peer, revoke=True)
        return

    if target is not None:
        try:
            await client.kick_participant(await event.get_chat(), target)
            return
        except Exception:
            logger.exception(".ban: could not remove target from the group; leaving instead")

    await client.delete_dialog(await event.get_chat())


async def _do_check(event: events.NewMessage.Event, ctx: WorkerContext, gate) -> None:
    quota = get_plan(gate.tariff).check_quota
    async with get_session() as db:
        result = await consume_check(db, ctx.owner_user_id, quota)
        await db.commit()
    if not result.allowed:
        await event.reply(t(ctx.owner_lang, "ub_check_limit", used=result.used, quota=quota))
        return
    # TODO(check): real lookup logic (spec pending). Quota accounting is live.
    note = t(ctx.owner_lang, "ub_check_note", used=result.used, quota=quota)
    await event.delete()
    if not await notify_owner(ctx, note):
        await event.respond(note)


async def clear_command(event) -> None:
    """Take a recognised command off the screen.

    Every dot-command is typed in a chat with someone else, so the command
    itself is the one thing that must not linger there — including when it
    does nothing, because a switched-off `.mute` still tells the other side
    what was attempted.
    """
    try:
        await event.delete()
    except Exception:
        logger.debug("could not remove a command message", exc_info=True)


async def _notice(client: TelegramClient, chat_id: int, text: str, *, delay: float = 2.0) -> None:
    """Post a limit/cooldown notice, then remove it — long enough to read,
    then out of the way instead of piling up as permanent chat clutter."""
    msg = await client.send_message(chat_id, text)
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        logger.debug(".send notice cleanup skipped", exc_info=True)


async def _do_send(client: TelegramClient, event: events.NewMessage.Event, ctx: WorkerContext, gate) -> None:
    count = int(event.pattern_match.group(1))
    text = event.pattern_match.group(2)
    plan = get_plan(gate.tariff)
    chat_id = event.chat_id

    # Cleared here rather than after the limits: every path below is one where
    # the command has been read and acted on, and a `.send 10 ...` left sitting
    # in a client's chat because a limit stopped it is the one outcome nobody
    # wants. The notices that follow explain what happened on their own.
    try:
        await event.delete()
    except Exception:
        logger.debug(".send: could not remove the command message", exc_info=True)

    if not 1 <= count <= plan.send_max_count:
        await _notice(client, chat_id, t(ctx.owner_lang, "ub_send_bad_count", max=plan.send_max_count))
        return

    # Per-user, not per-command-invocation: checked (and immediately marked)
    # BEFORE the send loop so two near-simultaneous .send calls can't both
    # slip through the gate while the first batch is still sending.
    async with get_session() as db:
        allowance = await peek_send_allowance(
            db,
            ctx.owner_user_id,
            cooldown_seconds=plan.send_cooldown_seconds,
            max_per_hour=plan.send_max_per_hour,
        )
        if allowance.allowed:
            await mark_send_used(db, ctx.owner_user_id)
            await db.commit()
        else:
            await db.rollback()
    if not allowance.allowed:
        minutes = max(1, ceil(allowance.remaining_seconds / 60))
        # Which limit stopped it matters: "wait 4 minutes" on an exhausted
        # hour just sends someone back to fail again.
        text = (
            t(ctx.owner_lang, "ub_send_hourly", limit=plan.send_max_per_hour, minutes=minutes)
            if allowance.reason == "hourly"
            else t(ctx.owner_lang, "ub_send_cooldown", minutes=minutes)
        )
        await _notice(client, chat_id, text)
        return

    for i in range(count):
        try:
            await client.send_message(chat_id, text)
        except errors.FloodWaitError:
            logger.warning(".send stopped early by FloodWaitError (%s of %s sent)", i, count)
            await client.send_message(chat_id, t(ctx.owner_lang, "ub_send_flood_stopped", sent=i, total=count))
            return


# --- helpers ---------------------------------------------------------------


async def _gate(event: events.NewMessage.Event, ctx: WorkerContext, command: str):
    async with get_session() as db:
        gate = await check_command(db, ctx.owner_user_id, command)
    if gate.allowed:
        return gate
    if gate.reason == "no_subscription":
        await event.reply(t(ctx.owner_lang, "ub_no_sub"))
    else:
        await event.reply(t(ctx.owner_lang, "ub_tariff_excludes"))
    return None


def _full_name(entity, lang: str = "uk") -> str:
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    name = " ".join(p for p in parts if p)
    return name or getattr(entity, "username", None) or t(lang, "ub_entity_user")
