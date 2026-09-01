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
    # Free ngrok accounts get one reserved static domain — set this to it and
    # the local Mini App URL stops changing on every bot restart (otherwise
    # any in-flight chat button / open tab dies with ERR_NGROK_3200). Get one
    # at dashboard.ngrok.com -> Domains. Irrelevant once this moves behind the
    # real moozuku.tech domain in prod.
    ngrok_domain: str | None = None
    admin_panel_port: int = 8080

    # Mini App connect flow. PUBLIC pair — served to the browser, readable
    # from devtools by design (part of the MTProto handshake, can't be
    # avoided). Deliberately separate from TELEGRAM_API_ID/HASH above, which
    # stay private and are what the userbot workers use to hold sessions
    # afterward — burning the public pair only stops new logins, nothing
    # already running is affected. Both unset (the default) means the /connect
    # handler falls back to the old in-chat keypad flow entirely — this is
    # what keeps production on the old flow with zero risk until these are
    # explicitly configured.
    webapp_api_id: int | None = None
    webapp_api_hash: str | None = None
    connect_web_port: int = 8081
    # Production: the real HTTPS origin a reverse proxy (Caddy) already
    # terminates in front of us. Set it and no tunnel is opened at all —
    # ngrok above exists only to give LOCAL testing an origin Telegram accepts.
    connect_public_url: str | None = None
    # Must be 0.0.0.0 inside a container, or a proxy on the host can never
    # reach the port; loopback by default so a dev machine doesn't serve this
    # to its whole network.
    connect_web_host: str = "127.0.0.1"

    # payments
    crypto_pay_token: str | None = None        # @CryptoBot Crypto Pay API token
    crypto_pay_testnet: bool = False
    crypto_pay_fiat: str = "USD"

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.replace(" ", "").split(",") if x}


settings = ManagementBotSettings()
