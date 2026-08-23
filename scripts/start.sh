#!/usr/bin/env bash
# Start the whole stack: Postgres -> migrations -> all three bots.
# Usage: bash scripts/start.sh    (Ctrl+C stops all bots)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Starting Postgres..."
docker compose up -d

echo "==> Waiting for Postgres to be healthy..."
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' usertgbot-postgres 2>/dev/null || echo "")
  [ "$status" = "healthy" ] && break
  sleep 1
done
if [ "${status:-}" != "healthy" ]; then
  echo "Postgres did not become healthy in time." >&2
  exit 1
fi

echo "==> Applying migrations..."
uv run alembic upgrade head

echo "==> Stopping any previously-running bots..."
bash "$(dirname "$0")/stop.sh" >/dev/null 2>&1 || true

mkdir -p logs
echo "==> Launching bots (output -> logs/*.log; Ctrl+C stops all)..."
pids=()
uv run management-bot >> logs/management_bot.log 2>&1 & pids+=($!)
uv run manager-bot    >> logs/manager_bot.log 2>&1 & pids+=($!)
uv run userbot-worker >> logs/userbot.log 2>&1 & pids+=($!)

echo "Tail a log:  tail -f logs/userbot.log"
trap 'echo; echo "Stopping..."; kill "${pids[@]}" 2>/dev/null || true; exit 0' INT TERM
wait
