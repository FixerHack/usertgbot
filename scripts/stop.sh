#!/usr/bin/env bash
# Stop any running usertgbot bots (management-bot, manager-bot, userbot-worker).
# Usage: bash scripts/stop.sh
for name in management-bot manager-bot userbot-worker; do
  pkill -f "$name" 2>/dev/null || true
done
echo "Stopped usertgbot bots (if any were running)."
