@echo off
REM Stop any running usertgbot bots (management-bot, manager-bot, userbot-worker).
REM Matches by process image name, not command-line substring, so this can
REM never self-match its own diagnostic command (see healthcheck.bat for why
REM that matters) and never kills an unrelated process that happens to
REM mention these names in its arguments.
REM Usage: scripts\stop.bat
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(management-bot|manager-bot|userbot-worker)\.exe$' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Stopped usertgbot bots (if any were running).
