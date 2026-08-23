"""Settings for the userbot worker pool."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class UserbotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_api_id: int
    telegram_api_hash: str
    encryption_key: str
    # optional: when set, workers push notifications to owners via this bot
    manager_bot_token: str | None = None


settings = UserbotSettings()
