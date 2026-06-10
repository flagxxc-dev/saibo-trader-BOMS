# Polymarket Arbitrage Bot — Technical Specification

**Stack:** C++ trading core · Next.js dashboard · WebAuthn-gated control plane
**Target markets:** Polymarket binary "Up or Down" markets (BTC/ETH/SOL/XRP, 5m & 15m windows)
**Reference price feed:** Binance WebSocket
**Status:** Specification draft

---

## 1. High-Level Architecture

The system is split into three tiers. The critical design rule: **the C++ trading core is never directly exposed to the internet**. The web layer can fail, get attacked, or restart without touching the trading engine.

```
┌─────────────────────────────────────────────────────────────┐
│                         Internet                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS (WebAuthn-gated)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Next.js Frontend + API Gateway (Node.js)                   │
│  - Auth, sessions, audit log                                │
│  - PostgreSQL (history) + Redis (hot state mirror)          │
│  - SSE/WS push to dashboard                                 │
└──────────────────────────┬──────────────────────────────────┘
                           │ Loopback / Unix socket / mTLS gRPC
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  C++ Trading Core                                            │
│  - Binance WS feed                                          │
│  - Polymarket WS + CLOB REST                                │
│  - Fair Value Model (sigmoid)                               │
│  - Signal Engine, Risk Manager, Order Router                │
│  - EIP-712 order signing (Polygon wallet)                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
            ┌──────────────┴──────────────┐
            ▼                             ▼
    ┌──────────────┐            ┌────────────────┐
    │   Binance    │            │   Polymarket   │
    │   WebSocket  │            │   CLOB + WS    │
    └──────────────┘            └────────────────┘
```

The frontend issues _intents_ ("pause strategy A", "set max position to X", "kill switch"). The C++ core decides whether to honor them and reports state back. **No order is ever placed because the frontend told it to in real time** — the frontend only configures and observes.

---

## 2. C++ Trading Core

### 2.1 Language & Build

- **C++20 minimum** (coroutines simplify async code dramatically)
- **Build system:** CMake + Conan or vcpkg
- **Compiler:** Clang 17+ or GCC 13+ with `-O3 -march=native -flto`
- **Sanitizers in CI:** AddressSanitizer, UBSan, ThreadSanitizer

### 2.2 Core Libraries

| Concern        | Library                    | Notes                                                           |
| -------------- | -------------------------- | --------------------------------------------------------------- |
| Async I/O      | Boost.Asio or `io_uring`   | Asio is mature; `io_uring` for absolute lowest latency on Linux |
| WebSockets     | uWebSockets or Boost.Beast | uWS is faster; Beast integrates cleaner with Asio               |
| TLS            | OpenSSL or BoringSSL       | BoringSSL for stricter behavior                                 |
| JSON parse     | simdjson                   | Multi-GB/s parsing, critical on hot path                        |
| JSON serialize | glaze or nlohmann          | glaze for performance, nlohmann for ergonomics                  |
| HTTP (REST)    | libcurl or cpr             | For Polymarket REST + Binance fallback                          |
| Crypto         | libsecp256k1 + Keccak      | EIP-712 typed-data signing for Polymarket CLOB on Polygon       |
| Logging        | spdlog (async sink)        | Lock-free, off-hot-path                                         |
| Metrics        | prometheus-cpp             | Scraped by Prometheus on the box                                |
| IPC            | gRPC or nanomsg            | Control plane to API gateway                                    |

### 2.3 Performance Targets

| Path                               | P50          | P99          |
| ---------------------------------- | ------------ | ------------ |
| Binance WS frame → signal decision | < 50 µs      | < 200 µs     |
| Signal → signed order on the wire  | < 300 µs     | < 1 ms       |
| Tick rate                          | event-driven | event-driven |
| Memory allocation in trading loop  | **zero**     | **zero**     |

Hot paths use pre-allocated ring buffers, fixed-size object pools, and `std::pmr` arena allocators. No `malloc`, no `std::string` copies, no virtual dispatch in the inner loop.

### 2.4 Module Breakdown

```
trading-core/
├── src/
│   ├── feeds/
│   │   ├── BinanceFeed.{h,cpp}         # WS, normalized ticks, in-mem book
│   │   └── PolymarketFeed.{h,cpp}      # WS for orderbook, REST for static
│   ├── model/
│   │   └── FairValueModel.{h,cpp}      # Time-aware sigmoid (port from Python)
│   ├── signals/
│   │   ├── LatencyArbDetector.{h,cpp}  # Strategy A
│   │   └── DumpHedgeDetector.{h,cpp}   # Strategy B (structural arb)
│   ├── risk/
│   │   └── RiskManager.{h,cpp}         # 4-layer failsafes
│   ├── exec/
│   │   ├── OrderRouter.{h,cpp}         # Async Boost.Beast HTTP POST, idempotency, slippage-simulated Paper Mode
│   │   └── EIP712Signer.{h,cpp}        # secp256k1 + Keccak struct hashing
│   ├── state/
│   │   └── StateStore.{h,cpp}          # In-mem state + snapshot
│   ├── control/
│   │   └── ControlPlane.{h,cpp}        # gRPC server, intent validation
│   └── main.cpp                        # Wiring, lifecycle, signal handling
├── tests/                              # GoogleTest, includes deterministic replay
├── CMakeLists.txt
└── conanfile.txt
```

### 2.5 Polymarket Order Signing — Important Caveat

Polymarket CLOB requires **EIP-712 signed orders** with a Polygon wallet. The implementation needs:

1. secp256k1 key handling (libsecp256k1)
2. Keccak-256 hashing
3. Exact EIP-712 domain separator + struct hash for Polymarket's `Order` type
4. RSV signature serialization

This is finicky but well-trodden. Two viable paths:

- **Path A (pure C++):** Hand-roll using libsecp256k1 + a Keccak implementation, with a reference test vector pulled from `ethers.js` to validate every step
- **Path B (FFI):** Wrap `alloy` (Rust) or `ethers-rs` via a thin C ABI shim. Lower implementation risk, slight build complexity cost

**Recommendation:** Path B for v1, optionally migrate to Path A once test vectors prove the pipeline.

### 2.6 Strategy Implementations

#### Strategy A — Latency Arbitrage

- Subscribe to Binance trade & book ticker streams for BTC/ETH/SOL/XRP
- Maintain a microsecond-resolution rolling window
- On each tick, recompute fair value via the time-aware sigmoid against the active Polymarket window's strike + remaining time
- If `|fair_value − polymarket_mid| > edge_threshold` and book depth is sufficient → emit intent
- RiskManager evaluates → OrderRouter signs FAK → submit

#### Strategy B — Structural Dump-Hedge

- Subscribe to Polymarket orderbook for both YES and NO tokens of each tracked market
- On every book update: `combined_ask = best_yes_ask + best_no_ask`
- If `combined_ask < 1.00 − fee_buffer` and depth covers desired size → emit dual-leg intent
- Both legs submitted in the same tick; if one fills and the other doesn't within timeout, immediately try to flatten the orphan leg (configurable)

### 2.7 Risk Engine — 4 Layers

1. **Per-trade position fraction cap** — no single position exceeds X% of bankroll
2. **Concurrent position limit** — max N open positions across all strategies
3. **Daily soft-halt** — cumulative realized + unrealized loss exceeds daily budget → pause new entries, keep managing open positions, reset at UTC midnight
4. **Drawdown hard-kill** — peak-to-trough equity drawdown exceeds threshold → halt all activity, require manual reset via dashboard with re-auth

Every order passes through all four checks. Failures emit a structured event to the audit log.

### 2.8 Position Sizing

- **Kelly Criterion** (fixed fraction or adaptive based on signal conviction)
- Capped by Layer 1 of the risk engine
- Configurable per-strategy multipliers

---

## 3. Next.js Frontend

### 3.1 Stack

- **Next.js 14+** with App Router
- **React Server Components** for the static dashboard shell
- **Auth:** Lucia Auth (lighter, transparent for single-user) or Auth.js
- **Real-time:** Server-Sent Events (SSE) from API gateway — sufficient for one-way state push, simpler than WS
- **State:** TanStack Query (server data) + Zustand (UI state)
- **Charts:** lightweight-charts (TradingView OSS) for prices, Recharts for P&L curves
- **Tables:** TanStack Table for trade log
- **Styling:** Tailwind + shadcn/ui

### 3.2 Pages

| Route         | Purpose                             | Auth Level                                  |
| ------------- | ----------------------------------- | ------------------------------------------- |
| `/login`      | WebAuthn challenge                  | Public                                      |
| `/dashboard`  | Live positions, edge calcs, P&L     | Authenticated                               |
| `/strategies` | Enable/disable, parameter overrides | Authenticated + fresh challenge for changes |
| `/risk`       | Kill switch, drawdown, daily budget | Authenticated + fresh challenge             |
| `/history`    | Trade log with filters              | Authenticated                               |
| `/audit`      | Append-only audit log viewer        | Authenticated                               |

### 3.3 API Gateway (within Next.js or separate Node process)

- **Framework:** Next.js Route Handlers or a dedicated Fastify process
- **Connects to C++ core** via gRPC over Unix socket (preferred) or localhost mTLS gRPC
- **Connects to Postgres** for persistent data, **Redis** for hot state mirror that the dashboard reads
- All state-changing endpoints log to the audit table before invoking the core

---

## 4. Authentication & Security

> If you put this online with anything weaker than what follows, assume the wallet gets drained. This is the section that matters most.

### 4.1 Authentication

- **WebAuthn / passkey as the only path** (YubiKey or platform authenticator). Not "an option" — the only one.
- Password + TOTP is acceptable as a fallback only if absolutely required, never as primary.
- Single-user system → no public registration flow; credentials provisioned out-of-band.
- Session tokens: short-lived (15 min) opaque tokens stored server-side, refreshed via re-auth.
- Sessions bound to IP + UA fingerprint; mismatch forces re-auth.

### 4.2 Network — The Single Biggest Win

**Strong recommendation: put the entire admin interface behind Tailscale or WireGuard.**

This single change eliminates >99% of the attack surface and costs nothing in UX. Public exposure is only justified if there's a hard requirement.

If public exposure is required:

- Cloudflare Tunnel + Cloudflare Access (Zero Trust)
- IP allowlist
- WAF rules
- TLS 1.3 only, HSTS preload, strict CSP, no inline scripts, SRI on any external assets

### 4.3 Authorization & Control

- Every state-changing endpoint requires a **fresh WebAuthn challenge**, not just a valid session — especially:
  - "Kill switch off"
  - "Raise position limit"
  - "Disable a failsafe"
  - "Change wallet address"
- **Action signing:** frontend signs sensitive intents with the passkey; API gateway verifies; C++ core verifies again before acting
- Two-person rule for the most dangerous actions if collaborators are ever added

### 4.4 Secrets Management

| Secret                 | Location                      | Notes                                                                                                    |
| ---------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------- |
| Polymarket private key | C++ core process only         | Loaded from sealed secret (Vault, KMS, or systemd `LoadCredentialEncrypted`). **Never** touches Node.js. |
| Binance API key        | C++ core, **read-only scope** | You only need market data — no trading scope = huge risk reduction                                       |
| Database credentials   | API gateway only              | Rotated quarterly                                                                                        |
| Session signing key    | API gateway only              | Rotated on any incident                                                                                  |

No secrets in committed env files. `.env.local` for dev, secrets manager in prod.

### 4.5 Operational Security

- Rate limit every endpoint, including authenticated ones
- Append-only audit log shipped to a **separate host** so a local compromise cannot erase tracks
- Alerts to your phone on:
  - Any login (success or failure)
  - Kill switch toggle
  - Position size over threshold
  - Daily loss > 50% of budget
  - C++ core heartbeat lost
- **Auto-kill on heartbeat loss:** if API gateway loses contact with the C++ core for > N seconds, the core itself halts new entries (someone unplugged monitoring? halt.)

---

## 5. Infrastructure

### 5.1 Hosting

- **Provider:** AWS Tokyo (`ap-northeast-1`) or Singapore (`ap-southeast-1`) for Binance proximity
- **Tier upgrade option:** Equinix Metal TY11 bare-metal for tail-latency-sensitive setups
- **Instance type:** c7i (or equivalent) — high single-core clock, modest RAM, NVMe local storage

### 5.2 OS Tuning (Linux)

- **OS:** Ubuntu 24.04 LTS or Debian 12 (configured for low-latency kernel `linux-image-lowlatency`).
- **Kernel isolation:** Pin the primary trading thread to dedicated, isolated cores via `isolcpus` and `taskset`. Ensure IRQs are bound to other cores.
- **Power Management:** Disable C-states (`intel_idle.max_cstate=0 processor.max_cstate=0`) and set CPU governor to `performance`.
- **Networking:** 
  - Enable busy-polling (`SO_BUSY_POLL`) to bypass the interrupt-driven network stack.
  - Increase NIC ring buffer sizes (`ethtool -G`).
  - Configure Receive Side Scaling (RSS) to route network interrupts to isolated cores.
- **Memory:** Disable transparent hugepages (`transparent_hugepage=never`) on the trading process to avoid non-deterministic compaction pauses. Use `numactl` to pin memory to the local socket.

### 5.3 Data Layer

- **PostgreSQL 16:** trade history, audit log, configuration history
- **Redis 7:** hot state mirror that the frontend polls/subscribes to (the C++ core publishes via pub/sub)
- Both run on the same host (single-user system, low ops overhead)

### 5.4 Process Supervision

- `systemd` unit per process: `trading-core.service`, `api-gateway.service`, `nextjs.service`
- `Restart=on-failure` with rate limiting
- Crash dumps captured to `/var/crash` with retention policy
- Log shipping to remote syslog or Loki

### 5.5 Monitoring

- Prometheus scrapes C++ core (prometheus-cpp), API gateway (prom-client), and node_exporter
- Grafana dashboards: latency histograms, edge calcs over time, P&L, risk-engine triggers
- Alertmanager → PagerDuty or `ntfy` for phone alerts

---

## 6. Build Plan

| Phase | Scope                                                        | Estimate                   |
| ----- | ------------------------------------------------------------ | -------------------------- |
| 1     | C++ core: Binance feed, Polymarket feed, basic state store   | 1.5 weeks                  |
| 2     | EIP-712 signing, OrderRouter, paper-trade harness            | 1 week                     |
| 3     | Port sigmoid model + risk engine, validate vs Python outputs | 1 week                     |
| 4     | Strategy detectors (latency arb + dump-hedge)                | 1 week                     |
| 5     | API gateway + gRPC contract                                  | 0.5 week                   |
| 6     | Next.js dashboard (read-only first, then controls)           | 1.5 weeks                  |
| 7     | WebAuthn, audit log, security hardening                      | 1 week                     |
| 8     | Deployment, OS tuning, monitoring                            | 0.5 week                   |
| 9     | **Paper-trading and tuning before live**                     | **1+ weeks — do not skip** |

**Total: 8–10 weeks** for one experienced developer.

---

## 7. One Thing to Decide Before Starting

### 7.1 Public web vs Tailscale

"Secure web authentication for this to exist online" is doable, but the failure mode is catastrophic — someone gets in, they have your trading wallet.

Tailscale gives you the same dashboard UX, eliminates ~all attack surface, and costs nothing. Unless there's a specific reason to expose it publicly (multiple users from many networks?), put it behind Tailscale and skip the entire public-internet threat model.

---

## 8. Open Questions

- Single wallet for both strategies, or separate wallets per strategy for clean accounting and blast-radius isolation?
- Withdraw automation — yes/no? (Strong recommendation: **no**. Manual withdrawals only, with re-auth.)
- Multi-region failover, or accept single-VPS risk? (For a single-user system, accepting single-VPS risk is fine; just have a documented manual restart procedure.)
- Backtesting harness scope — replay-from-logs only, or full historical reconstruction?

---

_End of specification._

## 9. Future Considerations

### 9.1 Polymarket Time Windows & Liquidity
The system currently targets 5-minute and 15-minute windows because Polymarket typically consolidates its deep liquidity there. However, **the smallest possible time window significantly improves capital efficiency and strategy odds**:
- **For Latency Arb:** Less time between entry and resolution means less "time risk" (market moving against your edge before resolution).
- **For Dump Hedge:** Shorter lock-ups mean higher capital velocity and compounding returns.

**Recommendation:** Target the smallest window that consistently maintains at least $5,000+ in resting liquidity near the mid-price. The C++ core should be built with dynamic market discovery to automatically pivot to shorter windows (e.g., 1-minute or 2-minute) if Polymarket introduces them with sufficient liquidity.
