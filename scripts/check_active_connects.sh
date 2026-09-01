#!/usr/bin/env bash
# How many Mini App connect attempts are in progress right now.
# Usage: bash scripts/check_active_connects.sh
set -u
cd "$(dirname "$0")/.." || exit 1
uv run python scripts/check_active_connects.py
