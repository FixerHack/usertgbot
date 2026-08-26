"""Owner-typed userbot commands: .info, .me, .ban, .check.

All are `outgoing=True` — the account owner types them in any chat and the
userbot (running as that account) acts. Every command is gated on the owner's
active subscription/tariff.
"""

from __future__ import annotations

import io
import logging

from telethon import TelegramClient, events
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.functions.users import GetFullUserRequest

from db.queries import consume_check
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
            title_or_name=_full_name(entity),
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
            title_or_name=getattr(entity, "title", "чат"),
            username=getattr(entity, "username", None),
            members_count=getattr(entity, "participants_count", None),
        )
    text = formatting.format_target_info(info)

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


def _full_name(entity) -> str:
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    name = " ".join(p for p in parts if p)
    return name or getattr(entity, "username", None) or "користувач"
