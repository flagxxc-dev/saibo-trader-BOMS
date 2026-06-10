@echo off
:: ══════════════════════════════════════════════════════════════════════════════
::  POLYMARKET — by Genoshide | polymarket arbitrage script bot
::  scripts\start.bat — launcher for Windows CMD / PowerShell
:: ══════════════════════════════════════════════════════════════════════════════
::
::  Usage (run from the repo root, or double-click):
::    scripts\start.bat           -- use PAPER_MODE from .env
::    scripts\start.bat paper     -- force paper (simulation) mode
::    scripts\start.bat live      -- force live mode (real funds)
::    scripts\start.bat health    -- run health check only
::
:: ══════════════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

:: ─── Change to repo root ──────────────────────────────────────────────────────
cd /d "%~dp0.."

set "MODE=%~1"

:: ─── Locate Python in venv ────────────────────────────────────────────────────
set "VENV_PY=venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo   [FAIL] Virtual environment not found at venv\Scripts\python.exe
    echo          Run scripts\install.bat first.
    pause
    exit /b 1
)

:: ─── Verify .env exists ───────────────────────────────────────────────────────
if not exist ".env" (
    if exist ".env.example" (
        echo   [WARN] .env not found -- copying .env.example to .env
        copy ".env.example" ".env" >nul
        echo          Edit .env before running in live mode.
    ) else (
        echo   [FAIL] .env not found. Run scripts\install.bat or create .env manually.
        pause
        exit /b 1
    )
)

:: ─── Dispatch ─────────────────────────────────────────────────────────────────
if /i "%MODE%"=="health" goto :health
if /i "%MODE%"=="paper"  goto :paper
if /i "%MODE%"=="live"   goto :live
if "%MODE%"==""          goto :default
echo   [FAIL] Unknown mode: '%MODE%'. Use: paper ^| live ^| health
pause
exit /b 1

:health
echo.
echo   POLYMARKET  Running health check...
echo   ──────────────────────────────────────────────────────────
"%VENV_PY%" healthcheck.py
pause
goto :eof

:paper
echo.
echo   POLYMARKET  Starting in PAPER mode  (simulation -- no real funds)
echo   ──────────────────────────────────────────────────────────
"%VENV_PY%" main.py --paper
goto :eof

:live
echo.
echo   POLYMARKET  Starting in LIVE mode
echo   ──────────────────────────────────────────────────────────
echo   WARNING: Real funds will be used.
echo   Press Ctrl+C within 5 seconds to abort.
echo.
ping -n 6 127.0.0.1 >nul
echo   Starting now...
"%VENV_PY%" main.py --live
goto :eof

:default
echo.
echo   POLYMARKET  Starting (mode from .env)
echo   ──────────────────────────────────────────────────────────
"%VENV_PY%" main.py
goto :eof
