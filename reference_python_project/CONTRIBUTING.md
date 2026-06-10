# Contributing

Pull requests are welcome. This is a live trading bot — correctness and safety matter more than feature velocity.

---

## Before You Start

If you want to work on a new feature or fix, open an issue first so we can align on the approach before you invest time writing code.

---

## Development Setup

```bash
git clone https://github.com/genoshide/polymarket-arbitrage-trading-bot.git
cd polymarket-arbitrage-trading-bot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fill in your credentials
```

Always develop against **paper mode** (`PAPER_MODE=true` in `.env`). Never commit credentials or live keys.

---

## Project Layout

```
core/
  polymarket_client.py   — CLOB REST: order placement, market discovery, price fetch
  polymarket_ws.py       — CLOB WebSocket: real-time price_change feed (/ws/market)
  edge_detector.py       — latency-arb signal: sigmoid model + 4-layer filter chain
  dump_hedge_detector.py — structural arb signal: YES+NO combined price scanner
  binance_ws.py          — Binance real-time price feed (latency_arb only)

risk/
  risk_manager.py        — drawdown limits, circuit breaker, per-asset stats
  kelly.py               — Kelly Criterion + adaptive fraction sizing

integration/
  telegram.py            — async alerting (thread pool, queue cap, proxy support)
  openclaw.py            — bidirectional AI agent commands

utils/
  dashboard.py           — Rich terminal UI
  logger.py              — structured logging

main.py                  — orchestrator: trading loop, position management, lifecycle
config.py                — all env vars + BotConfig dataclass
```

---

## Code Style

- Python 3.9+ — no walrus operator or 3.10+ match statements
- Type hints on all public functions and class attributes
- No inline comments that describe *what* the code does — only *why* (non-obvious constraints, workarounds, subtle invariants)
- No `datetime.utcnow()` — use `datetime.now(timezone.utc)`
- Async-first: all I/O paths must be `async`/`await`. No blocking calls on the event loop.
- Log with `logger.info/warning/debug` — never `print()`

---

## Testing Requirements

There is no automated test suite. Before submitting a PR, include evidence in the PR description that your change works against the live environment:

| Change type | Required evidence |
|---|---|
| WebSocket protocol / URL | Log trace showing messages received after subscribe |
| REST / order execution | Paper mode log showing order placed and filled |
| Risk logic | Description of scenario + before/after behavior |
| Config / env var | `.env.example` updated with the new variable |
| Dashboard / UI | Screenshot |
| Bug fix | Description of how to reproduce the original bug + confirmation it no longer occurs |

**WebSocket changes in particular must be tested live.** The Polymarket CLOB WS API has changed endpoints before without notice — never assume a format is correct without a live connection trace.

---

## PR Guidelines

- One logical change per PR. Mixing a bug fix with a refactor makes review harder.
- PR title: use conventional commits — `fix:`, `feat:`, `refactor:`, `docs:`, `chore:`
- Keep `main.py` changes minimal — prefer pushing logic into the relevant subsystem file
- Never add `# type: ignore` without a comment explaining why
- Do not change `PAPER_MODE` default or any risk parameter defaults without discussion

### Changes that always need a maintainer discussion first

- New strategy type
- Changes to Kelly sizing logic
- Changes to circuit breaker thresholds
- New mandatory dependencies

---

## Submitting

1. Fork the repo and create a branch from `main`
2. Make your changes with the testing evidence above
3. Open a PR against `main` — describe what the change does, why it's needed, and how you tested it
4. A maintainer will review within a few days

---

## Areas That Need Help

High-value open items:

- **15-minute window support** — edge detection tuning for slower windows
- **ETH / SOL / XRP signal calibration** — sigmoid model currently tuned for BTC
- **Backtesting harness** — replay historical Polymarket price events against the edge detector
- **Docker health check** — `HEALTHCHECK` in Dockerfile that verifies WS connectivity
- **Binance US / alternative feed** — fallback for regions where Binance is blocked
