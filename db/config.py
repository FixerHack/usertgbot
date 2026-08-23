"""Database connection settings, shared by all services."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/usertgbot"


db_settings = DBSettings()
