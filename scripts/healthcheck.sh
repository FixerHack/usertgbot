#!/usr/bin/env bash
# Health check: verifies the environment is ready to run the bots.
# Usage: bash scripts/healthcheck.sh   (exit code 0 = all good)
set -u
cd "$(dirname "$0")/.." || exit 1

fail=0
ok()   { echo "[ OK ] $1"; }
bad()  { echo "[FAIL] $1"; fail=1; }
warn() { echo "[WARN] $1"; }

echo "=== usertgbot health check ==="

# 1. uv
if command -v uv >/dev/null 2>&1; then ok "uv installed"; else bad "uv not found (https://docs.astral.sh/uv/)"; fi

# 2. docker + postgres container
if command -v docker >/dev/null 2>&1; then
  status=$(docker inspect -f '{{.State.Health.Status}}' usertgbot-postgres 2>/dev/null || echo "")
  if [ "$status" = "healthy" ]; then ok "postgres container healthy"
  elif [ -n "$status" ]; then bad "postgres container status: $status"
  else bad "postgres container not running -> docker compose up -d"; fi
else bad "docker not found"; fi

# 3. .env presence + required keys
if [ -f .env ]; then
  ok ".env present"
  for key in DB_URL BOT_TOKEN MANAGER_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH ENCRYPTION_KEY; do
    if grep -q "^${key}=." .env; then ok "  ${key} set"; else bad "  ${key} missing in .env"; fi
  done

  echo "--- optional (feature-gated) ---"
  if grep -q "^MANAGER_BOT_USERNAME=." .env; then ok "  MANAGER_BOT_USERNAME set"; else warn "  MANAGER_BOT_USERNAME not set (connect success message won't link the manager bot)"; fi
  if grep -q "^SUPPORT_CONTACT=." .env; then ok "  SUPPORT_CONTACT set"; else warn "  SUPPORT_CONTACT not set (🆘 Підтримка will show a placeholder)"; fi
  if grep -q "^ADMIN_IDS=." .env; then ok "  ADMIN_IDS set"; else warn "  ADMIN_IDS not set (/admin will be inaccessible to everyone)"; fi
  if grep -q "^NGROK_AUTHTOKEN=." .env; then ok "  NGROK_AUTHTOKEN set"; else warn "  NGROK_AUTHTOKEN not set (/admin panel will only be reachable on localhost)"; fi
  if grep -q "^CRYPTO_PAY_TOKEN=." .env; then ok "  CRYPTO_PAY_TOKEN set"; else warn "  CRYPTO_PAY_TOKEN not set (crypto payment option hidden; Telegram Stars still works)"; fi
else
  bad ".env missing -> cp .env.example .env"
fi

# 4. migrations at head
if uv run alembic current 2>/dev/null | grep -q '(head)'; then
  ok "migrations at head"
else
  bad "migrations not at head -> uv run alembic upgrade head"
fi

# 5. duplicate bot instances (causes TelegramConflictError from Telegram)
running=$(pgrep -f 'management-bot|manager-bot|userbot-worker' 2>/dev/null | wc -l | tr -d ' ')
if [ "${running:-0}" -gt 0 ]; then
  warn "$running bot process(es) already running — starting more will cause TelegramConflictError. Run scripts/stop.sh first if you're about to start.sh again."
else
  ok "no bot processes currently running"
fi

# 6. ngrok python SDK importable (used by /admin, not the deprecated pyngrok)
if uv run python -c "import ngrok" >/dev/null 2>&1; then
  ok "ngrok SDK importable"
else
  warn "ngrok SDK not importable — run 'uv sync' (needed for the /admin panel's public tunnel)"
fi

# 7. tariff card images (optional — cards fall back to text without them)
img_count=$(ls assets/tariffs/*.{jpg,jpeg,png,webp} 2>/dev/null | wc -l | tr -d ' ')
if [ "${img_count:-0}" -gt 0 ]; then
  ok "tariff images: $img_count found in assets/tariffs/"
else
  warn "no tariff images in assets/tariffs/ — /subscribe cards will be text-only"
fi

echo
if [ "$fail" -eq 0 ]; then echo "All required checks passed ✅ (see WARN lines for optional features not configured)"; else echo "Some required checks failed ❌"; fi
exit "$fail"
