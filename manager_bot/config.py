"""Settings for the personal manager bot."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ManagerBotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    manager_bot_token: str


settings = ManagerBotSettings()
