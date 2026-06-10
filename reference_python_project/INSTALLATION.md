# INSTALLATION GUIDE

> Setup instructions for the Polymarket Arbitrage Bot on Linux, Windows, and Docker.
> For strategy explanations and configuration reference, see [README.md](README.md).

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Network & VPS Requirements](#network--vps-requirements)
- [Linux / Ubuntu](#linux--ubuntu)
- [Windows](#windows)
- [Docker (Any OS)](#docker-any-os)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Troubleshooting](#troubleshooting)
- [Security](#security)

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Python 3.9+ | Python 3.11 recommended |
| Polymarket account | With USDC on **Polygon mainnet** (not Ethereum mainnet) |
| Wallet private key | For signing orders — stored only in `.env` |
| Minimum balance | ~$5 USDC recommended |
| Stable internet | Low latency is critical — a VPS near exchange servers is strongly recommended |
| Docker (optional) | For containerised deployment |

> **Geographic restriction**: Polymarket is not available to US residents. Check the legal status in your jurisdiction before depositing funds.

---

## Network & VPS Requirements

Network quality directly affects signal accuracy and execution speed.

### Why Network Matters

The bot depends on two real-time WebSocket connections:

1. **Binance WebSocket** — streams live asset prices. A 100ms delay means the bot sees price moves late, reducing the usable lag window.
2. **Polymarket WebSocket** — streams live order book prices. If blocked, the bot falls back to REST (~200–500ms), increasing stale price risk.

### Geo-Restrictions

| Service | Restriction | Symptom |
|---------|-------------|---------|
| Polymarket CLOB WebSocket | Blocked in some regions (HTTP 403/451) | Log: `PM WS: Connection rejected` — falls back to REST |
| Polymarket REST API | Generally accessible | May be slow from distant regions |
| Binance WebSocket | Blocked for US IPs | `ConnectionResetError` on startup |

If you see:
```
PM WS: Connection rejected (HTTP 403/451) — falling back to REST-only mode
```
The bot continues in REST-only mode, but price updates are slower (~200–500ms instead of ~20ms).

### Recommended VPS

| Provider | Location | Latency to Binance | Cost |
|----------|----------|--------------------|------|
| DigitalOcean | Singapore (`sgp1`) | ~5–15ms | $6/mo |
| Vultr | Singapore | ~5–15ms | $6/mo |
| Hetzner | Singapore | ~10–20ms | $5/mo |
| AWS | ap-southeast-1 | ~5–10ms | ~$8/mo |
| Contabo | Singapore | ~10–20ms | $5/mo |

Singapore is optimal: no Polymarket geo-blocks and closest to Binance's exchange node.

### Minimum VPS Specs

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 1 vCPU | 2 vCPU |
| RAM | 512 MB | 1 GB |
| Bandwidth | 1 TB/mo | 2 TB/mo |
| OS | Ubuntu 20.04+ | Ubuntu 22.04 LTS |

---

## Linux / Ubuntu

### Option A — One-command installer (recommended)

```bash
git clone https://github.com/genoshide/polymarket-arbitrage-trading-bot.git
cd polymarket-arbitrage-trading-bot
chmod +x scripts/install.sh
./scripts/install.sh
```

The script will:
1. Detect Python 3.9+ automatically
2. Create `./venv` virtual environment
3. Install all dependencies
4. Copy `.env.example` → `.env`
5. Run the health check

Then edit your config and start:

```bash
nano .env                    # fill in your credentials
./scripts/start.sh paper     # paper (simulation) mode
./scripts/start.sh live      # live mode (real funds)
```

### Option B — Manual steps

```bash
# 1. Install Python (if not already installed)
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip -y

# 2. Clone the repository
git clone https://github.com/genoshide/polymarket-arbitrage-trading-bot.git
cd polymarket-arbitrage-trading-bot

# 3. Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# 4. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 5. Configure
cp .env.example .env
nano .env    # fill in your credentials

# 6. Health check (optional but recommended)
python healthcheck.py

# 7. Run
python main.py --paper    # paper mode
python main.py --live     # live mode
```

### Option C — Makefile

```bash
make install    # create venv + install deps
make setup      # copy .env.example → .env
make health     # run health check
make paper      # start paper mode
make live       # start live mode
```

### Running as a background service (systemd)

To keep the bot running after you log out:

```bash
sudo nano /etc/systemd/system/polymarket-arb-bot.service
```

Paste the following (replace `/home/youruser/polymarket-arbitrage-trading-bot` with your actual path):

```ini
[Unit]
Description=Polymarket Arbitrage Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/polymarket-arbitrage-trading-bot
ExecStart=/home/youruser/polymarket-arbitrage-trading-bot/venv/bin/python main.py --live
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-arb-bot
sudo systemctl start polymarket-arb-bot
sudo systemctl status polymarket-arb-bot    # verify it's running
journalctl -u polymarket-arb-bot -f         # tail logs
```

### VPS Quick Setup (Ubuntu 22.04)

```bash
ssh root@YOUR_VPS_IP
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
git clone https://github.com/genoshide/polymarket-arbitrage-trading-bot.git
cd polymarket-arbitrage-trading-bot
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env

# Run with screen (keeps running after SSH disconnect)
screen -S bot
python main.py --live
# Detach: Ctrl+A then D
# Reattach: screen -r bot
```

---

## Windows

### Option A — One-command installer (recommended)

Open **Command Prompt** or **PowerShell** as a normal user (not Administrator):

```cmd
git clone https://github.com/genoshide/polymarket-arbitrage-trading-bot.git
cd polymarket-arbitrage-trading-bot
scripts\install.bat
```

Then:

```cmd
notepad .env               :: fill in your credentials
scripts\start.bat paper    :: paper mode
scripts\start.bat live     :: live mode
```

### Option B — Git Bash

If you have [Git for Windows](https://gitforwindows.org/) installed:

```bash
git clone https://github.com/genoshide/polymarket-arbitrage-trading-bot.git
cd polymarket-arbitrage-trading-bot
./scripts/install.sh
./scripts/start.sh paper
```

### Option C — Manual steps (CMD)

```cmd
:: 1. Install Python from https://python.org (check "Add to PATH")

:: 2. Clone
git clone https://github.com/genoshide/polymarket-arbitrage-trading-bot.git
cd polymarket-arbitrage-trading-bot

:: 3. Create virtual environment
python -m venv venv
venv\Scripts\activate

:: 4. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

:: 5. Configure
copy .env.example .env
notepad .env

:: 6. Run
python main.py --paper
```

> **Windows note**: Use [Windows Terminal](https://aka.ms/terminal) for correct colour and Unicode rendering. The classic `cmd.exe` displays garbled output.

### Windows Connection Notes

The `WinError 64 — The specified network name is no longer available` error in Binance WebSocket logs is a normal transient Windows network event. The reconnect logic handles it automatically.

If it happens repeatedly at startup:
- Check Windows Defender Firewall is not blocking Python
- Check antivirus is not intercepting WebSocket traffic
- Use Windows Terminal instead of CMD

---

## Docker (Any OS)

Works identically on Linux, macOS, and Windows. Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine.

### Quick start

```bash
# 1. Configure
cp .env.example .env
nano .env     # (or notepad .env on Windows)

# 2. Build image
docker build -t polymarket-arbitrage-trading-bot .

# 3. Run paper mode (interactive, with dashboard)
docker compose run --rm -it bot --paper

# 4. Run as background daemon
docker compose up -d
```

### All Docker commands

| Command | What it does |
|---------|-------------|
| `make docker-build` | Build the image |
| `make docker-paper` | Paper mode, interactive (dashboard visible) |
| `make docker-live` | Live mode, interactive (5-second abort window) |
| `make docker-up` | Start as background daemon |
| `make docker-stop` | Stop the container |
| `make docker-logs` | Tail live log output |
| `make docker-health` | Run healthcheck inside container |
| `make docker-shell` | Open bash shell inside container |
| `make docker-clean` | Remove image, volumes, containers |

### Docker notes

- Credentials are loaded from `.env` at runtime — never baked into the image
- Logs are persisted to `./logs/polymarket_bot.log` via a bind mount
- The dashboard is only visible in interactive mode (`-it`). In daemon mode the bot logs to file only
- `docker stop` triggers a clean shutdown — the bot has 15 seconds to close open positions

---

## Configuration

All settings live in `.env`. Run `cp .env.example .env` to create it from the template.

### Polymarket Credentials

```env
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
POLYMARKET_FUNDER=0xYOUR_WALLET_ADDRESS_HERE
POLYMARKET_SIGNATURE_TYPE=1
```

| Variable | Description | Required |
|----------|-------------|----------|
| `POLYMARKET_PRIVATE_KEY` | Wallet private key for signing orders | **Live only** |
| `POLYMARKET_FUNDER` | Wallet address holding your USDC on Polygon | **Live only** |
| `POLYMARKET_SIGNATURE_TYPE` | `1` = EIP-712 proxy (Gnosis Safe) · `0` = EOA direct | Yes |

To find your wallet address: open MetaMask → copy the address shown at the top.

### Telegram Setup

1. Open Telegram → search `@BotFather` → send `/newbot`
2. Follow prompts → copy the token → paste into `TELEGRAM_BOT_TOKEN`
3. Open `@userinfobot` → it replies with your numeric ID → paste into `TELEGRAM_CHAT_ID`

For a full description of every configuration parameter and what it means, see [README.md](README.md).

---

## Running the Bot

```bash
# Paper mode (simulation — no real funds)
python main.py --paper

# Live mode (real funds — use with caution)
python main.py --live

# Use PAPER_MODE setting from .env
python main.py

# Override log verbosity
python main.py --paper --log-level DEBUG

# Override starting balance (paper mode only)
python main.py --paper --balance 500
```

### Using scripts

**Linux / macOS / Git Bash:**
```bash
./scripts/start.sh paper
./scripts/start.sh live
./scripts/start.sh health
```

**Windows CMD:**
```cmd
scripts\start.bat paper
scripts\start.bat live
scripts\start.bat health
```

---

## Troubleshooting

### Bot exits immediately / no output

```bash
python healthcheck.py
```

Common causes:
- `.env` file missing → `cp .env.example .env`
- `POLYMARKET_PRIVATE_KEY` not set and `PAPER_MODE=false`
- Missing packages → `pip install -r requirements.txt`
- `TRADE_WINDOW_MINUTES` is not `5` or `15`

### Bot detects no signals

| Cause | Fix |
|-------|-----|
| Market below liquidity threshold | Lower `EDGE_MIN_MARKET_LIQUIDITY` to `300` |
| Entry zone too narrow | Widen `EDGE_MIN_ENTRY_PRICE` / `EDGE_MAX_ENTRY_PRICE` |
| Fair value strength too high | Lower `EDGE_MIN_FAIR_VALUE_STRENGTH` to `0.03` |
| Threshold too high | Lower `EDGE_MIN_EDGE_THRESHOLD` to `0.04` |
| Cooldown active | Check log — `EDGE_COOLDOWN_SECONDS` may be long |
| Price not moving | Normal during low-volatility periods |
| Market time expired | Bot blocked in last 20% of window |

### `Kelly returned None` / no trades opening

Most common cause: balance too small for the position fraction.

```
Balance $3.00 × fraction 0.30 = $0.90 < $1.00 minimum → blocked
Balance $3.00 × fraction 0.40 = $1.20 ≥ $1.00         → OK ✓
```

Fix: raise `RISK_MAX_POSITION_FRACTION` or deposit more USDC.

### Polymarket WebSocket not connecting

```
PM WS: Connection rejected (HTTP 403/451) — falling back to REST-only mode
```

Geo-restriction on the WebSocket endpoint. The bot continues in REST-only mode.
Permanent fix: run on a VPS in Singapore.

### Dashboard not rendering (Windows)

Use [Windows Terminal](https://aka.ms/terminal). The classic `cmd.exe` does not support the alternate screen buffer.

---

## Security

| Rule | Details |
|------|---------|
| ✅ Private key in `.env` only | Never hardcode keys in source files |
| ✅ `.env` in `.gitignore` | Will never be committed to version control |
| ✅ `.env` not baked into Docker image | Injected at runtime via `env_file` |
| ✅ Dedicated trading wallet | Use a wallet created specifically for this bot |
| ✅ Minimum USDC only | Only deposit the amount you intend to trade |
| ❌ Never commit `.env` | Not to GitHub, Gist, Pastebin, or Discord |
| ❌ Never share your private key | With anyone, ever |
| ❌ Never run on a shared computer | Your `.env` could be read by other users |

**Recommended setup:**
1. Create a fresh MetaMask wallet specifically for this bot
2. Transfer only the USDC you want to trade (e.g. $20–$50 to start)
3. Keep your main wallet completely separate
4. Run in paper mode for at least 1 week before funding the trading wallet
