"""Entrypoint for the userbot worker pool (supervisor).

Run independently: `uv run userbot-worker`

Runs forever: polls the DB for active sessions and keeps exactly one worker
task per active session. New connections are picked up automatically, workers
for deactivated sessions are stopped, and crashed workers are restarted on the
next poll — so starting with zero sessions is fine, it just waits.
"""

import asyncio
import logging

from sqlalchemy import select

from db.models import Session, User
from db.session import get_session
from shared.logging_conf import setup_logging
from userbot.crypto import decrypt_session
from userbot.worker import run_worker

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 15


async def _load_active_sessions() -> list[tuple[int, int, int, str, str]]:
    """Return (session_id, owner_user_id, owner_telegram_id, lang, session_string) tuples."""
    async with get_session() as db:
        rows = (
            await db.execute(
                select(Session, User.telegram_id, User.language_code)
                .join(User, Session.user_id == User.id)
                .where(Session.is_active.is_(True))
            )
        ).all()
        loaded: list[tuple[int, int, int, str, str]] = []
        for session, telegram_id, language_code in rows:
            try:
                loaded.append(
                    (session.id, session.user_id, telegram_id, language_code or "uk",
                     decrypt_session(session.encrypted_session))
                )
            except Exception:
                logger.exception("could not decrypt session_id=%s; skipping", session.id)
        return loaded


async def _supervise() -> None:
    running: dict[int, asyncio.Task] = {}
    logger.info("userbot supervisor started; polling every %ss for active sessions", POLL_INTERVAL_SECONDS)

    while True:
        try:
            sessions = await _load_active_sessions()
        except Exception as exc:
            # transient (e.g. Postgres still starting) — one concise line, no stack spam
            logger.warning("DB not reachable (%s); retrying in %ss", type(exc).__name__, POLL_INTERVAL_SECONDS)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        active = {s[0]: s for s in sessions}

        # reap finished/crashed workers so they can be restarted below
        for session_id in list(running):
            task = running[session_id]
            if task.done():
                if not task.cancelled() and task.exception() is not None:
                    logger.error("worker session_id=%s crashed: %r", session_id, task.exception())
                del running[session_id]

        # stop workers whose session was deactivated/removed
        for session_id in list(running):
            if session_id not in active:
                running[session_id].cancel()
                del running[session_id]
                logger.info("stopped worker for deactivated session_id=%s", session_id)

        # start workers for newly-active sessions
        for session_id, (sid, owner_user_id, telegram_id, lang, session_string) in active.items():
            if session_id not in running:
                running[session_id] = asyncio.create_task(
                    run_worker(sid, owner_user_id, telegram_id, lang, session_string)
                )
                logger.info("started worker for session_id=%s", session_id)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def run() -> None:
    setup_logging("userbot")
    await _supervise()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("userbot supervisor stopped")


if __name__ == "__main__":
    main()
