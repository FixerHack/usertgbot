"""One TelegramClient worker per active client session."""

import logging

from telethon import TelegramClient
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
                logger.warning("session_id=%s not authorized; deactivating", session_id)
                async with get_session() as db:
                    await deactivate_session(db, session_id)
                    await db.commit()
                await notify_owner(ctx, t(lang, "session_invalid"))  # once — supervisor won't restart it
                return
            logger.info("worker started for session_id=%s owner_user_id=%s", session_id, owner_user_id)
            await client.run_until_disconnected()
    finally:
        if notifier is not None:
            await notifier.close()
        await ctx.cache.close()
