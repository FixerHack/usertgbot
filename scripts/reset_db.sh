#!/usr/bin/env bash
# Wipe the database and reapply migrations from scratch.
# Stops all bots FIRST so nothing holds a connection or polls mid-reset
# (avoids "relation does not exist" spam while the schema is being rebuilt).
# Usage: bash scripts/reset_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Stopping bots (clean disconnect before touching the DB)..."
bash scripts/stop.sh >/dev/null 2>&1 || true
sleep 1

echo "==> Dropping and recreating the public schema..."
docker exec usertgbot-postgres psql -U usertgbot -d usertgbot -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null

echo "==> Reapplying migrations..."
uv run alembic upgrade head

echo "Done. Database reset to a clean state (bots are stopped — start.sh to bring them back up)."
