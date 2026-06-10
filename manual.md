# Polymarket HFT Bot: Master Deployment Manual

This manual contains the complete technical definition, setup instructions, and management commands for the C++ Polymarket Arbitrage Bot.

---

## 1. Technical Overview
* **Definition**: A High-Frequency Trading (HFT) engine built in C++20 for sub-10ms execution.
* **Core Logic**: Uses a **Time-Aware Sigmoid Model** to exploit the 2.7-second price propagation delay between Binance (Global Discovery) and Polymarket (Prediction Market).
* **Safety**: Includes an EIP-712 signing engine for gasless orders and a comprehensive Risk Manager with Kelly Position Sizing and Slippage Tracking.

---

## 2. System Prerequisites
Run this command on your Cloud Linux (Ubuntu/Debian) instance to install all necessary compilers, libraries, and utilities:

```bash
sudo apt-get update && sudo apt-get install -y build-essential cmake libssl-dev python3-pip unzip screen && \
pip3 install websockets rich spdlog boost
```

---

## 3. Project Installation (ZIP Method)
1. **Upload** the `polymarket-arbitrage-trading-bot.zip` to your server.
2. **Extract and set permissions**:
    ```bash
    unzip polymarket-arbitrage-trading-bot.zip
    cd polymarket-arbitrage-trading-bot
    chmod +x *.sh
    ```

---

## 4. Environment Configuration
You must configure your wallet and trading mode before the first run.
```bash
nano .env
```
**Update these specific values:**
* `PAPER_MODE=true` (Set to `false` only after testing).
* `POLYMARKET_FUNDER=0x...` (Your wallet address).
* `POLYMARKET_PRIVATE_KEY=...` (Your private key).
* `LIVE_STARTING_BALANCE=50.0` (Your test budget).

---

## 5. Background Execution (24/7 Trading)
To keep the bot running after you disconnect from the server, use `screen`.

1. **Start Session**: `screen -S bot`
2. **Build Core**: `./build.sh`
3. **Launch Bot**: `./start.sh`
4. **Go Background**: Press **`Ctrl + A`** then **`D`**. (Bot is now safely detached).

---

## 6. Profit & Performance Monitoring

### Re-attach to Live Dashboard:
```bash
screen -r bot
```

### Detailed Profit List (LA Trades):
```bash
grep "Position CLOSED" bot.log | awk -F'|' '{print $1, $3, $4}'
```

### Total Cumulative Profit Calculation:
```bash
grep "PnL:" bot.log | awk -F'PnL: ' '{print $2}' | awk -F' | ' '{print $1}' | sed 's/\$//g' | awk '{sum+=$1} END {print "Total: $" sum}'
```

---

## 7. Management Cheat Sheet

| Task | Command / Shortcut |
| :--- | :--- |
| **Hide UI (Keep Running)** | `Ctrl + A` then `D` |
| **Check Live Logs** | `tail -f bot.log` |
| **Force Kill Bot** | `pkill -f trading-core` |
| **Reset/Update Files** | `unzip -o bot.zip && ./build.sh` |
| **Switch Paper/Live** | Change `PAPER_MODE` in `.env` and restart |

---

## 8. "Live Ready" Safety Check
Before moving from Paper to Live, check that your `bot.log` shows **Slippage %**. If slippage is consistently over 2.5%, go to `.env` and increase `min_price_move` for BTC to `35.0` to ensure higher conviction entries.
