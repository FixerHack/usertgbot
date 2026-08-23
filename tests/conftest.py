"""Test setup: inject dummy settings *before* management_bot imports, and
provide an in-memory SQLite session factory for storage tests.

Env vars take precedence over the real .env in pydantic-settings, so the bot
config (and Fernet key) is deterministic and independent of the developer's
local secrets.
"""

import os

from cryptography.fernet import Fernet

# Must run before any `management_bot.config` import.
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()
os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("MANAGER_BOT_TOKEN", "123456:test-manager-token")
os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "test-hash")
os.environ["ENCRYPTION_KEY"] = TEST_ENCRYPTION_KEY

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from db.models import Base


@pytest_asyncio.fixture
async def db_session():
    """A fresh in-memory SQLite AsyncSession with all tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()
