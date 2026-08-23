@echo off
REM Health check: verifies the environment is ready to run the bots.
REM Usage: scripts\healthcheck.bat   (exit code 0 = all good)
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set FAIL=0

echo === usertgbot health check ===

where uv >nul 2>&1
if %errorlevel%==0 (echo [ OK ] uv installed) else (echo [FAIL] uv not found & set FAIL=1)

set PG=
for /f "delims=" %%i in ('docker inspect -f "{{.State.Health.Status}}" usertgbot-postgres 2^>nul') do set PG=%%i
if "!PG!"=="healthy" (
  echo [ OK ] postgres container healthy
) else if "!PG!"=="" (
  echo [FAIL] postgres container not running -^> docker compose up -d
  set FAIL=1
) else (
  echo [FAIL] postgres container status: !PG!
  set FAIL=1
)

if exist .env (
  echo [ OK ] .env present
  for %%K in (DB_URL BOT_TOKEN MANAGER_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH ENCRYPTION_KEY) do (
    findstr /r /c:"^%%K=.." .env >nul
    if !errorlevel!==0 (echo [ OK ]   %%K set) else (echo [FAIL]   %%K missing in .env & set FAIL=1)
  )

  echo --- optional: feature-gated ---
  findstr /r /c:"^MANAGER_BOT_USERNAME=.." .env >nul
  if !errorlevel!==0 (echo [ OK ]   MANAGER_BOT_USERNAME set) else (echo [WARN]   MANAGER_BOT_USERNAME not set - connect success message wont link the manager bot)
  findstr /r /c:"^SUPPORT_CONTACT=.." .env >nul
  if !errorlevel!==0 (echo [ OK ]   SUPPORT_CONTACT set) else (echo [WARN]   SUPPORT_CONTACT not set - Support button will show a placeholder)
  findstr /r /c:"^ADMIN_IDS=.." .env >nul
  if !errorlevel!==0 (echo [ OK ]   ADMIN_IDS set) else (echo [WARN]   ADMIN_IDS not set - /admin will be inaccessible to everyone)
  findstr /r /c:"^NGROK_AUTHTOKEN=.." .env >nul
  if !errorlevel!==0 (echo [ OK ]   NGROK_AUTHTOKEN set) else (echo [WARN]   NGROK_AUTHTOKEN not set - /admin panel only reachable on localhost)
  findstr /r /c:"^CRYPTO_PAY_TOKEN=.." .env >nul
  if !errorlevel!==0 (echo [ OK ]   CRYPTO_PAY_TOKEN set) else (echo [WARN]   CRYPTO_PAY_TOKEN not set - crypto payment option hidden, Stars still works)
) else (
  echo [FAIL] .env missing -^> copy .env.example .env
  set FAIL=1
)

uv run alembic current 2>nul | findstr /c:"(head)" >nul
if !errorlevel!==0 (echo [ OK ] migrations at head) else (echo [FAIL] migrations not at head -^> uv run alembic upgrade head & set FAIL=1)

REM Match by process image name (not command-line substring) — a substring
REM match would catch this very diagnostic command's own text and always
REM self-report as "running", since the search pattern text itself contains
REM the words being searched for. The @(...) wrapper forces array context so
REM .Count is reliable even when exactly one process matches (PowerShell
REM otherwise unwraps a single match to a bare object with no .Count).
set RUNNING=0
for /f %%c in ('powershell -NoProfile -Command "(@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(management-bot|manager-bot|userbot-worker)\.exe$' })).Count"') do set RUNNING=%%c
if "!RUNNING!"=="0" (
  echo [ OK ] no bot processes currently running
) else (
  echo [WARN] !RUNNING! bot process^(es^) already running - starting more will cause TelegramConflictError. Run scripts\stop.bat first if you're about to start.bat again.
)

uv run python -c "import ngrok" >nul 2>&1
if !errorlevel!==0 (echo [ OK ] ngrok SDK importable) else (echo [WARN] ngrok SDK not importable - run 'uv sync' ^(needed for the /admin panel's public tunnel^))

set IMGCOUNT=0
for %%f in (assets\tariffs\*.jpg assets\tariffs\*.jpeg assets\tariffs\*.png assets\tariffs\*.webp) do (
  if exist "%%f" set /a IMGCOUNT+=1
)
if !IMGCOUNT! GTR 0 (
  echo [ OK ] tariff images: !IMGCOUNT! found in assets\tariffs\
) else (
  echo [WARN] no tariff images in assets\tariffs\ - /subscribe cards will be text-only
)

echo.
if !FAIL!==0 (echo All required checks passed ^(see WARN lines for optional features not configured^)) else (echo Some required checks failed)
exit /b !FAIL!
