"""FastAPI app for the admin panel.

`create_app(token)` builds the app; the token guards the /api/* routes (passed
as an `X-Token` header or `?token=` query by the dashboard). The DB session is a
FastAPI dependency so tests can override it with an in-memory SQLite session.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from admin_web import service
from admin_web.page import PAGE
from db.session import get_session


async def _default_db() -> AsyncIterator[AsyncSession]:
    async with get_session() as session:
        yield session


def create_app(token: str, *, db_dependency=_default_db) -> FastAPI:
    app = FastAPI(title="usertgbot admin", docs_url=None, redoc_url=None)

    def require_token(x_token: str | None = Header(default=None), token_q: str | None = Query(default=None, alias="token")) -> None:
        provided = x_token or token_q or ""
        # constant-time compare to avoid leaking the token via response timing
        if not secrets.compare_digest(provided, token):
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return PAGE

    @app.get("/api/metrics", dependencies=[Depends(require_token)])
    async def metrics(db: AsyncSession = Depends(db_dependency)) -> dict:
        return (await service.get_metrics(db)).to_dict()

    @app.get("/api/users", dependencies=[Depends(require_token)])
    async def users(
        db: AsyncSession = Depends(db_dependency),
        tariff: str | None = None,
        connected: int | None = None,
        blocked: int | None = None,
        joined_after: date | None = None,
        joined_before: date | None = None,
    ) -> list[dict]:
        rows = await service.list_users(
            db,
            tariff=tariff or None,
            connected=None if connected is None else bool(connected),
            blocked=None if blocked is None else bool(blocked),
            joined_after=joined_after,
            joined_before=joined_before,
        )
        return [r.to_dict() for r in rows]

    @app.post("/api/users/{telegram_id}/grant", dependencies=[Depends(require_token)])
    async def grant(telegram_id: int, tariff: str, days: int = 30, db: AsyncSession = Depends(db_dependency)) -> dict:
        try:
            await service.grant_subscription(db, telegram_id, tariff, days=days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        await db.commit()
        return {"ok": True}

    @app.post("/api/users/{telegram_id}/revoke", dependencies=[Depends(require_token)])
    async def revoke(telegram_id: int, db: AsyncSession = Depends(db_dependency)) -> dict:
        n = await service.revoke_subscription(db, telegram_id)
        await db.commit()
        return {"ok": True, "revoked": n}

    @app.post("/api/users/{telegram_id}/block", dependencies=[Depends(require_token)])
    async def block(telegram_id: int, db: AsyncSession = Depends(db_dependency)) -> dict:
        ok = await service.set_blocked(db, telegram_id, True)
        await db.commit()
        return {"ok": ok}

    @app.post("/api/users/{telegram_id}/unblock", dependencies=[Depends(require_token)])
    async def unblock(telegram_id: int, db: AsyncSession = Depends(db_dependency)) -> dict:
        ok = await service.set_blocked(db, telegram_id, False)
        await db.commit()
        return {"ok": ok}

    return app
