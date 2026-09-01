@echo off
REM How many Mini App connect attempts are in progress right now.
REM Usage: scripts\check_active_connects.bat
setlocal
cd /d "%~dp0.."
uv run python scripts\check_active_connects.py
