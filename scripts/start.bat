@echo off
REM Start the whole stack: Postgres -> migrations -> all three bots.
REM Each bot opens in its own window. Close a window to stop that bot.
setlocal enabledelayedexpansion
cd /d "%~dp0.."

echo ==^> Starting Postgres...
docker compose up -d
if errorlevel 1 (echo Failed to start Postgres & exit /b 1)

echo ==^> Waiting for Postgres to be healthy...
set /a tries=0
:waitpg
set PG=
for /f "delims=" %%i in ('docker inspect -f "{{.State.Health.Status}}" usertgbot-postgres 2^>nul') do set PG=%%i
if "!PG!"=="healthy" goto pgready
set /a tries+=1
if !tries! geq 30 (echo Postgres did not become healthy in time & exit /b 1)
timeout /t 1 /nobreak >nul
goto waitpg
:pgready
echo [ OK ] Postgres healthy

echo ==^> Applying migrations...
uv run alembic upgrade head
if errorlevel 1 (echo Migration failed & exit /b 1)

if not exist logs mkdir logs

echo ==^> Stopping any previously-running bots...
call "%~dp0stop.bat"

echo ==^> Launching bots in background (output -^> logs\*.log)...
start "management-bot" /b cmd /c "uv run management-bot >> logs\management_bot.log 2>&1"
start "manager-bot"    /b cmd /c "uv run manager-bot >> logs\manager_bot.log 2>&1"
start "userbot-worker" /b cmd /c "uv run userbot-worker >> logs\userbot.log 2>&1"

echo.
echo All three launched (background). Logs:
echo   logs\management_bot.log
echo   logs\manager_bot.log
echo   logs\userbot.log
echo Tail a log:  powershell Get-Content logs\userbot.log -Wait -Tail 20
echo Stop them:   scripts\stop.bat
echo Tip: for separate VS Code panels run the task "Start all bots" (Ctrl+Shift+P -^> Run Task).
endlocal
