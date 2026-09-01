"""How many Mini App connect attempts are live right now.

For local testing only: restarting management_bot restarts ngrok too, which
hands out a brand new random URL (free tier) — any button/tab pointing at the
old one goes dead. This just answers "is it safe to restart" — safe meaning
"nothing will be silently orphaned", not that the DB rows themselves would be
harmed (they survive a restart fine either way).

Usage: uv run python scripts/check_active_connects.py
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from db.models import ConnectToken
from db.session import get_session


async def main() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with get_session() as db:
        rows = (
            await db.execute(
                select(ConnectToken)
                .where(ConnectToken.used_at.is_(None), ConnectToken.expires_at > now)
                .order_by(ConnectToken.expires_at)
            )
        ).scalars().all()

    if not rows:
        print("No active connect attempts - safe to restart.")
        return

    print(f"{len(rows)} active connect attempt(s) - restarting will strand their Mini App URL:")
    for row in rows:
        left = row.expires_at - now
        minutes, seconds = divmod(int(left.total_seconds()), 60)
        print(f"  telegram_id={row.telegram_id}  {minutes}m{seconds:02d}s left  (token created {row.created_at})")


if __name__ == "__main__":
    asyncio.run(main())
