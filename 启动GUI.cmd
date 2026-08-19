@echo off
setlocal
cd /d "%~dp0"
title BOSS Job Search GUI

echo ========================================
echo BOSS Job Search - Setup and Launch
echo ========================================
echo.

where uv >nul 2>nul
if errorlevel 1 goto :no_uv

if not exist ".env" (
    if not exist ".env.example" goto :no_env_example
    copy /y ".env.example" ".env" >nul
    echo [First run] Created .env from .env.example
)

echo [1/2] Checking Python environment and dependencies...
call uv sync --locked
if errorlevel 1 goto :sync_failed

echo.
echo [2/2] Starting GUI...
call uv run --no-sync python -m boss_zhipin.tauri
if errorlevel 1 goto :gui_failed

echo.
echo GUI has closed normally.
goto :finish

:no_uv
echo [ERROR] uv was not found in PATH.
echo Install uv, then run this file again:
echo https://docs.astral.sh/uv/getting-started/installation/
goto :failed

:no_env_example
echo [ERROR] .env.example was not found next to this script.
goto :failed

:sync_failed
echo.
echo [ERROR] Failed to create or update the Python environment.
goto :failed

:gui_failed
echo.
echo [ERROR] GUI failed to start.

:failed
echo Keep this window open and send the error above for diagnosis.

:finish
echo.
pause
endlocal
