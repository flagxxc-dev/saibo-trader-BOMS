@echo off
:: ══════════════════════════════════════════════════════════════════════════════
::  POLYMARKET — by Genoshide | polymarket arbitrage script bot
::  scripts\install.bat — one-command installer for Windows CMD / PowerShell
:: ══════════════════════════════════════════════════════════════════════════════
::
::  Usage (run from the repo root):
::    scripts\install.bat
::
::  What it does:
::    1. Checks Python 3.9+ is available
::    2. Creates a virtual environment in .\venv
::    3. Upgrades pip, setuptools, wheel
::    4. Installs all dependencies from requirements.txt
::    5. Copies .env.example -> .env if .env doesn't exist
::    6. Runs the health check
:: ══════════════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

:: ─── Change to repo root (parent of scripts\) ─────────────────────────────
cd /d "%~dp0.."

echo.
echo   POLYMARKET by Genoshide  .  polymarket arbitrage script bot
echo   Installer
echo   ──────────────────────────────────────────────────────────
echo.

:: ─── Step 1: Find Python 3.9+ ────────────────────────────────────────────────
echo   ^> Checking Python version...

set "PYTHON="
for %%P in (python3.12 python3.11 python3.10 python3.9 python3 python py) do (
    where %%P >nul 2>&1
    if !errorlevel! == 0 (
        for /f "tokens=*" %%V in ('%%P -c "import sys; v=sys.version_info; ok=v>=(3,9); print(f'{v.major}.{v.minor}.{v.micro}' if ok else '')" 2^>nul') do (
            if not "%%V"=="" (
                set "PYTHON=%%P"
                set "PY_VER=%%V"
                goto :found_python
            )
        )
    )
)

echo   [FAIL] Python 3.9 or higher not found.
echo          Install from https://www.python.org/downloads/ and re-run.
exit /b 1

:found_python
echo   [PASS] Python !PY_VER! found.

:: ─── Step 2: Create virtual environment ──────────────────────────────────────
echo   ^> Creating virtual environment in .\venv ...

if exist "venv\" (
    echo   [WARN] venv\ already exists -- skipping creation.
) else (
    %PYTHON% -m venv venv
    if errorlevel 1 (
        echo   [FAIL] Failed to create virtual environment.
        exit /b 1
    )
    echo   [PASS] Virtual environment created.
)

set "VENV_PY=venv\Scripts\python.exe"
set "VENV_PIP=venv\Scripts\pip.exe"

if not exist "%VENV_PY%" (
    echo   [FAIL] venv\Scripts\python.exe not found.
    exit /b 1
)

:: ─── Step 3: Upgrade pip ──────────────────────────────────────────────────────
echo   ^> Upgrading pip, setuptools, wheel...
"%VENV_PIP%" install --upgrade pip setuptools wheel --quiet
if errorlevel 1 (
    echo   [WARN] pip upgrade returned an error -- continuing anyway.
) else (
    echo   [PASS] pip upgraded.
)

:: ─── Step 4: Install dependencies ────────────────────────────────────────────
echo   ^> Installing dependencies from requirements.txt...
"%VENV_PIP%" install -r requirements.txt
if errorlevel 1 (
    echo   [FAIL] Dependency installation failed.
    exit /b 1
)
echo   [PASS] Dependencies installed.

:: ─── Step 5: Create .env ─────────────────────────────────────────────────────
echo   ──────────────────────────────────────────────────────────
if exist ".env" (
    echo   [WARN] .env already exists -- skipping. Edit it manually if needed.
) else (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo   [PASS] .env created from .env.example
        echo.
        echo   ACTION REQUIRED: Open .env and fill in your credentials.
        echo   For paper trading: nothing required (PAPER_MODE=true by default).
        echo   For live trading:  set POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER.
    ) else (
        echo   [WARN] .env.example not found -- create .env manually.
    )
)

:: ─── Step 6: Health check ─────────────────────────────────────────────────────
echo   ──────────────────────────────────────────────────────────
echo   ^> Running pre-flight health check...
echo.
"%VENV_PY%" healthcheck.py
:: health check prints its own pass/fail; ignore exit code here

:: ─── Done ────────────────────────────────────────────────────────────────────
echo.
echo   ──────────────────────────────────────────────────────────
echo   Installation complete.
echo.
echo   Next steps:
echo     1. Edit .env with your settings (if not already done)
echo     2. Run  scripts\start.bat paper   to start in paper mode
echo     3. Run  scripts\start.bat live    to start in live mode
echo     4. Or use  python main.py --paper / --live  directly
echo.

endlocal
