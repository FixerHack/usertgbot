"""One TelegramClient worker per active client session."""

import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from telethon import TelegramClient, errors
from telethon.errors.common import AuthKeyNotFound
from telethon.sessions import StringSession

from db.queries import deactivate_session
from db.session import get_session
from shared.i18n import resolve_lang, t
from shared.notify import ManagerNotifier
from userbot.config import settings
from userbot.context import WorkerContext
from userbot.handlers import autoresponder, autosave, commands, viewonce
from userbot.message_cache import RecentMessageCache
from userbot.notify import notify_owner

logger = logging.getLogger(__name__)

# Every way Telegram can tell us "this session is gone for good": revoked from
# another device, password changed, account banned/deleted, or the auth key
# simply not recognised any more. AuthKeyNotFound is the important one — it is
# raised by connect() itself, BEFORE is_user_authorized() ever runs, so
# checking authorization alone silently missed the most common real case and
# left the supervisor restarting a doomed worker every 15s while the owner was
# never told anything.
_DEAD_SESSION_ERRORS = (
    AuthKeyNotFound,
    errors.AuthKeyUnregisteredError,
    errors.AuthKeyDuplicatedError,
    errors.SessionExpiredError,
    errors.SessionRevokedError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)


def _relink_keyboard(lang: str) -> InlineKeyboardMarkup | None:
    """One-tap "reconnect" button on the session-expired notice — without it
    the owner has to find Settings -> Link account on their own, which is
    exactly the friction that makes people give up on a paid subscription."""
    if not settings.management_bot_username:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=t(lang, "btn_relink_account"),
                url=f"https://t.me/{settings.management_bot_username}?start=connect",
            )
        ]]
    )


async def _mark_session_dead(ctx: WorkerContext, session_id: int, lang: str, reason: str) -> None:
    """Deactivate the session and tell the owner, exactly once."""
    logger.warning("session_id=%s is dead (%s); deactivating", session_id, reason)
    async with get_session() as db:
        await deactivate_session(db, session_id)
        await db.commit()
    await notify_owner(ctx, t(lang, "session_invalid"), reply_markup=_relink_keyboard(lang))


async def run_worker(
    session_id: int,
    owner_user_id: int,
    owner_telegram_id: int,
    owner_lang: str,
    decrypted_session_string: str,
) -> None:
    lang = resolve_lang(owner_lang)
    client = TelegramClient(
        session=StringSession(decrypted_session_string),
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    notifier = ManagerNotifier(settings.manager_bot_token) if settings.manager_bot_token else None
    ctx = WorkerContext(
        session_id=session_id,
        owner_user_id=owner_user_id,
        owner_telegram_id=owner_telegram_id,
        owner_lang=lang,
        notifier=notifier,
        cache=RecentMessageCache(owner_user_id=owner_user_id, db_path=settings.cache_db_path),
    )

    commands.register(client, ctx)
    autosave.register(client, ctx)
    autoresponder.register(client, ctx)
    viewonce.register(client, ctx)

    try:
        async with client:
            # string session may be revoked (logout / password change / ban)
            if not await client.is_user_authorized():
                await _mark_session_dead(ctx, session_id, lang, "not authorized")
                return
            logger.info("worker started for session_id=%s owner_user_id=%s", session_id, owner_user_id)
            await client.run_until_disconnected()
    except _DEAD_SESSION_ERRORS as exc:
        # Covers both connect() refusing the auth key up front and the session
        # being killed mid-flight while run_until_disconnected() holds it.
        await _mark_session_dead(ctx, session_id, lang, type(exc).__name__)
    finally:
        if notifier is not None:
            await notifier.close()
        await ctx.cache.close()
