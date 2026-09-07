# usertgbot

A Telegram userbot platform split into independently-deployable services that
share one Postgres schema. A user subscribes via a bot, connects their own
Telegram account, and then gets a set of in-chat `.`-commands plus a private
"manager bot" feed of what happens on their account.

For what the product is, what it charges, how it is deployed and why the
decisions behind it were made, see [`PROJECT.md`](PROJECT.md). Running work and
test rounds live in [`TASKS.md`](TASKS.md). This file is the technical entry
point: layout, setup, and how to run things.

## Services

| Package | Runtime | Role |
| --- | --- | --- |
| `management_bot` | aiogram (Bot API) | Main public bot: `/start`, tariffs & payments, account linking, `/settings`, `/help`, `/support`, `/admin`. Also hosts the three web apps below in its own event loop. |
| `manager_bot` | aiogram (Bot API) | Personal notification bot — DMs each owner their deleted/edited messages, autoresponder hits, `.info`/`.check` results, one-time media, chat transcripts. |
| `userbot` | Telethon (user account) | Supervised worker pool — one client per connected account; runs the in-chat `.`-commands and observes events. |
| `connect_web` | FastAPI | Mini App account login (GramJS in the browser); mounts `pay_web` at `/pay` and `landing` at `/`. |
| `pay_web` | FastAPI | Card payment page and the WayForPay server-to-server callback. |
| `landing` | FastAPI | Public one-page site; prices rendered from the same source the bot sells from. |
| `admin_web` | FastAPI + ngrok | On-demand admin panel (metrics + user management), launched from `/admin`. |
| `db` | SQLAlchemy 2.0 async + Alembic | Shared models, session factory, cross-service queries. Imports nothing from services. |
| `shared` | — | Cross-service domain logic: tariffs, pricing, i18n, settings schema, transcripts, manager-bot delivery, logging. |
| `db_maintenance` | Typer CLI | Standalone integrity check / fix / restore with pluggable sources & profiles. |

The three web apps share one uvicorn inside the `management_bot` process, mounted
at `/connect`, `/pay` and `/` — registration order is what stops the root
landing from swallowing the two more specific routes.

## Features

- **Tariffs & payments** — Standard / Pro / Premium. Fixed net profit per tariff
  in UAH; USD and Stars are derived from it at the live NBU rate. Pay with
  **Telegram Stars**, **Crypto Pay (@CryptoBot)** or **WayForPay** (UAH cards,
  gateway-driven auto-renewal). Card prices stay round — the ~2% comes out of
  margin rather than being grossed up.
- **Account linking** — Telegram will not let a code delivered inside Telegram
  be entered normally, so login runs in a **Mini App**: GramJS creates the auth
  key in the user's own browser and the server never sees the code or the 2FA
  password. `initData` is signature-checked and must name the same user the
  one-time token was issued to. Falls back to the older in-chat keypad flow when
  `WEBAPP_API_ID`/`WEBAPP_API_HASH` are unset — that pair of variables is the
  whole switch, there is no separate feature flag. Sessions are
  **Fernet-encrypted** before they hit the DB.
- **In-chat commands** (from a connected account, each one deletes itself):
  `.info`, `.me`, `.ban`, `.check` (monthly quota), `.send N text` (Pro+, capped
  per call, per cooldown and per hour), `.mute` / `.unmute` (Pro+),
  `.save` / `.unsave` (Premium, transcribes a chat to a .txt),
  `.clone` / `.stopc` (Premium, echoes someone's messages back).
- **Manager bot feed** — auto-saved deleted/edited messages and media,
  autoresponder (Pro) hits, one-time photos/voice/video notes, `.info`/`.check`
  results, chat transcripts. "Ignore this chat" and "Delete" buttons on notices,
  plus an ignored-chats list in settings.
- **Settings** — profile card (`.me`), autoresponder, ignored chats, language,
  13 feature switches (one per auto-save type and per tariff command), and a
  chat-recording screen that starts a recording through Telegram's own contact
  picker.
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

- **Bot identity:** `MANAGEMENT_BOT_USERNAME`, `MANAGER_BOT_USERNAME`,
  `SUPPORT_CONTACT`, `ADMIN_IDS`. The usernames do **not** follow the tokens —
  change a token and you must change these by hand, or links will point at a bot
  that no longer exists.
- **Mini App login:** `WEBAPP_API_ID`, `WEBAPP_API_HASH` (a second api pair,
  separate from the workers' — its `api_hash` is served to the page and is
  therefore public by design), `CONNECT_PUBLIC_URL`, `CONNECT_WEB_PORT`,
  `CONNECT_WEB_HOST`.
- **HTTPS for local Mini App work:** `NGROK_AUTHTOKEN`, `NGROK_DOMAIN` (a
  reserved domain keeps the URL stable across restarts). Telegram refuses to
  open a Mini App over plain http.
- **Payments:** `CRYPTO_PAY_TOKEN` (+ `CRYPTO_PAY_TESTNET`, `CRYPTO_PAY_FIAT`),
  `WAYFORPAY_MERCHANT_ACCOUNT`, `WAYFORPAY_SECRET_KEY`, `WAYFORPAY_DOMAIN`,
  `WAYFORPAY_MERCHANT_PASSWORD` (a distinct value from the secret key — it
  authenticates the auto-renewal cancellation call).
- **Admin panel:** `ADMIN_PANEL_PORT`.

Each one gates a feature rather than breaking startup: no `CRYPTO_PAY_TOKEN`
hides the crypto option, no WayForPay values hide the hryvnia option, no
`WEBAPP_API_*` falls back to the keypad login. See `.env.example` for the full
notes.

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
uv run pytest -q         # 487 tests
uv run alembic check     # models vs migrations, no drift
```

Both are part of the pre-deploy routine. `alembic check` earns its place: a
model changed without a migration passes every unit test and fails in
production.

## Deploy

Production runs on a VPS under Docker Compose (`docker-compose.prod.yml`):
Postgres, a one-shot `migrate`, and the three bot services, all from one
`usertgbot:latest` image. Caddy terminates TLS on the public domain and proxies
to the web apps.

There is no CI/CD — deploys are a tarball, an atomic directory swap (the old
tree is kept for rollback), an image rebuild, an explicit migration run, and a
service restart, followed by smoke checks. Two things that are easy to get
wrong:

1. **Move `data/`, don't copy it** — and stop `userbot-worker` first. The
   anti-delete cache can be gigabytes.
2. **The cache's WAL can grow without bound** if a connection leaks: an open
   read connection blocks checkpointing. With the worker stopped, one
   `PRAGMA wal_checkpoint(TRUNCATE)` reclaims it.

The full procedure, with the smoke-check list, is in [`PROJECT.md`](PROJECT.md).
