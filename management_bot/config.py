"""Settings for management_bot, loaded from environment / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ManagementBotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    telegram_api_id: int
    telegram_api_hash: str
    encryption_key: str
    # shown to users; optional
    manager_bot_username: str | None = None   # e.g. "personmanbot" (no @)
    support_contact: str = "@your_support"     # manager/support handle

    # admin panel
    admin_ids: str = ""                        # comma-separated Telegram ids
    ngrok_authtoken: str | None = None
    admin_panel_port: int = 8080

    # payments
    crypto_pay_token: str | None = None        # @CryptoBot Crypto Pay API token
    crypto_pay_testnet: bool = False
    crypto_pay_fiat: str = "USD"

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.replace(" ", "").split(",") if x}


settings = ManagementBotSettings()
