"""Subscription/tariff gating for userbot commands.

The 'owner' is the user who connected this session (Session.user_id). A command
runs only if the owner has an active subscription whose tariff includes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from db.queries import get_active_subscription_for_user
from shared.tariffs import Tariff, tariff_grants_command


@dataclass
class GateResult:
    allowed: bool
    tariff: Tariff | None = None
    reason: str = ""


async def check_command(
    session: AsyncSession,
    owner_user_id: int,
    command: str,
    *,
    now: datetime | None = None,
) -> GateResult:
    sub = await get_active_subscription_for_user(session, owner_user_id, now=now)
    if sub is None:
        return GateResult(allowed=False, reason="no_subscription")
    tariff = Tariff(sub.tariff)
    if not tariff_grants_command(tariff, command):
        return GateResult(allowed=False, tariff=tariff, reason="tariff_excludes")
    return GateResult(allowed=True, tariff=tariff)
