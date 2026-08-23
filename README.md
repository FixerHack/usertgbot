# usertgbot

A Telegram userbot platform split into independently-deployable services that
share one Postgres schema. A user subscribes via a bot, connects their own
Telegram account, and then gets a set of in-chat `.`-commands plus a private
"manager bot" feed of what happens on their account.

## Services

| Package | Runtime | Role |
| --- | --- | --- |
| `management_bot` | aiogram (Bot API) | Main public bot: `/start`, tariffs & payments, `/connect`, `/settings`, `/help`, `/support`, `/admin`. |
| `manager_bot` | aiogram (Bot API) | Personal notification bot — DMs each owner their deleted/edited messages, autoresponder hits, `.info`/`.check` results, one-time media. |
| `userbot` | Telethon (user account) | Supervised worker pool — one client per connected account; runs the in-chat `.`-commands and observes events. |
| `admin_web` | FastAPI + ngrok | On-demand admin panel (metrics + user management), launched from `/admin`. |
| `db` | SQLAlchemy 2.0 async + Alembic | Shared models, session factory, cross-service queries. Imports nothing from services. |
| `shared` | — | Cross-service domain logic: tariffs, pricing, i18n, settings schema, manager-bot delivery, logging. |
| `db_maintenance` | Typer CLI | Standalone integrity check / fix / restore with pluggable sources & profiles. |

## Features

- **Tariffs & payments** — Standard / Pro / Premium(WIP). Fixed net profit per
  tariff; prices computed live from the NBU USD/UAH rate and grossed up per
  channel. Pay with **Telegram Stars** or **Crypto Pay (@CryptoBot)**.
- **Account linking** — Telegram blocks logging in with a code delivered inside
  Telegram, so the code is entered via an inline number pad. The exported
  Telethon session is **Fernet-encrypted** before it hits the DB.
- **In-chat commands** (from a connected account): `.info` (user/chat summary +
  1 avatar, phone, bio), `.me` (your card), `.ban` (block+delete / kick), `.check`
  (quota-limited).
- **Manager bot feed** — auto-saved deleted/edited messages, autoresponder (Pro)
  hits, one-time (view-once) photos/voice, `.info`/`.check` results. "Ignore this
  chat" button + an ignored-chats list in settings.
- **Admin panel** — metrics (users, subs by tariff, 7-day activity chart, top
  users), user cards with filters and grant/revoke/ban actions.
- **i18n** — UK / RU / EN, auto-selected from the Telegram `language_code`.

## Setup

```bash
docker compose up -d          # local Postgres 16
cp .env.example .env          # fill in the values (see below)
uv run alembic upgrade head   # apply migrations
```

Generate the session-encryption key referenced by `.env`:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Required `.env`

`DB_URL`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (my.telegram.org), `BOT_TOKEN`
(main bot), `MANAGER_BOT_TOKEN` (manager bot), `ENCRYPTION_KEY`.

### Optional `.env`

`MANAGER_BOT_USERNAME`, `SUPPORT_CONTACT`, `ADMIN_IDS`, `NGROK_AUTHTOKEN`,
`CRYPTO_PAY_TOKEN` (+ `CRYPTO_PAY_TESTNET`, `CRYPTO_PAY_FIAT`). See `.env.example`
for what each one gates.

## Run

Scripts (Windows `.bat`, macOS/Linux `.sh`):

```bash
scripts/healthcheck.sh   # verify env, Postgres, migrations, deps
scripts/start.sh         # Postgres -> migrations -> all bots (logs in logs/)
scripts/stop.sh          # stop all bots
scripts/reset_db.sh      # stop bots, wipe DB, re-migrate
```

Or run a single service directly:

```bash
uv run management-bot
uv run manager-bot
uv run userbot-worker
uv run db-maintenance check
```

VS Code: `Run Task → Start all bots` runs them as integrated-terminal panels.

## Tests

```bash
uv run pytest
```
