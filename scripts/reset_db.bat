@echo off
REM Wipe the database and reapply migrations from scratch.
REM Stops all bots FIRST so nothing holds a connection or polls mid-reset
REM (avoids "relation does not exist" spam while the schema is being rebuilt).
REM Usage: scripts\reset_db.bat
setlocal
cd /d "%~dp0.."

echo ==^> Stopping bots (clean disconnect before touching the DB)...
call "%~dp0stop.bat"
timeout /t 1 /nobreak >nul

echo ==^> Dropping and recreating the public schema...
docker exec usertgbot-postgres psql -U usertgbot -d usertgbot -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >nul
if errorlevel 1 (echo Failed to reset schema - is Postgres running? & exit /b 1)

echo ==^> Reapplying migrations...
uv run alembic upgrade head
if errorlevel 1 (echo Migration failed & exit /b 1)

echo Done. Database reset to a clean state (bots are stopped - start.bat to bring them back up).
endlocal
