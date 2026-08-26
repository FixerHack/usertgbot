"""Read-side DB access for the userbot (settings + media the owner configured)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MediaBlob, UserSettings


async def _settings(session: AsyncSession, owner_user_id: int) -> UserSettings | None:
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == owner_user_id)
    )
    return result.scalar_one_or_none()


async def load_me_card(session: AsyncSession, owner_user_id: int) -> dict | None:
    row = await _settings(session, owner_user_id)
    return row.me_card if row else None


async def load_autoresponder(session: AsyncSession, owner_user_id: int) -> dict | None:
    row = await _settings(session, owner_user_id)
    return row.autoresponder if row else None


async def load_features(session: AsyncSession, owner_user_id: int) -> dict | None:
    row = await _settings(session, owner_user_id)
    return row.features if row else None


async def load_media(session: AsyncSession, owner_user_id: int, purpose: str) -> bytes | None:
    result = await session.execute(
        select(MediaBlob.data).where(
            MediaBlob.owner_user_id == owner_user_id, MediaBlob.purpose == purpose
        )
    )
    return result.scalar_one_or_none()
