Write-Host "Starting Polymarket Arbitrage Trading Core with Dashboard Bridge..." -ForegroundColor Cyan

# Check if binary exists
if (-not (Test-Path "build/trading-core.exe")) {
    Write-Host "Error: C++ binary not found. Please build the project first." -ForegroundColor Red
    exit 1
}

$ErrorActionPreference = "Stop"

Write-Host "Deriving L2 API Keys..."
python derive_and_update_keys.py

Write-Host "Starting CLI Dashboard Bridge..."
$BridgeJob = Start-Job {
    Set-Location $using:PWD
    python dashboard_bridge.py
}

Write-Host "Bridge started. Waiting for initialization..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

Write-Host "Launching CLI Dashboard..." -ForegroundColor Green
python cli_dashboard.py

# Cleanup
Write-Host "Shutting down..." -ForegroundColor Yellow
Get-Process "trading-core" -ErrorAction SilentlyContinue | Stop-Process -Force
