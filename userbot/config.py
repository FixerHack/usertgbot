"""Settings for the userbot worker pool."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class UserbotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_api_id: int
    telegram_api_hash: str
    encryption_key: str
    # The main bot's own token. Used for the session-expired notice
    # specifically: unlike the manager bot, the owner is GUARANTEED to
    # already have a chat with this one (it's how they connected in the
    # first place), whereas the manager-bot chat is a separate opt-in step
    # many people skip — confirmed live: that notice silently vanished when
    # only sent through an unstarted manager-bot chat.
    bot_token: str
    # optional: when set, workers push notifications to owners via this bot
    manager_bot_token: str | None = None
    # Username of the MANAGEMENT bot (no @). Only used to build a "re-link
    # your account" deep link on the session-expired notice, so the owner can
    # fix it in one tap instead of hunting through Settings.
    management_bot_username: str | None = None
    # SQLite file backing the recent-message cache (anti-delete/edit) — a
    # process restart must NOT lose messages that are "in flight" between
    # being received and possibly deleted, so this lives on disk, not RAM.
    cache_db_path: str = "data/message_cache.sqlite3"


settings = UserbotSettings()
