#!/usr/bin/env python3
"""
Dual-strategy shadow observer — real CLOB asks, simulated fills, no live orders.

Strategy A (cheap_sweep): buy cheaper side <=0.45 in dynamic batches (max 20sh).
Strategy B (mm_dca): DCA both sides when ask<=0.89; endgame gradual hedge without 0.99 panic buys.

Capital: $2000 total = $1000 per strategy (independent budgets).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
CLOB = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com").rstrip("/")
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

Side = Literal["yes", "no"]

FEE_RATE = float(os.getenv("FEE_RATE", "0.018"))
TOTAL_BANKROLL = float(os.getenv("SHADOW_TOTAL_USDC", "2000"))
STRAT_BUDGET = float(os.getenv("SHADOW_STRAT_BUDGET", "1000"))
A_RESERVE = float(os.getenv("SHADOW_A_RESERVE", "200"))
A_MAX_GAP = float(os.getenv("SHADOW_A_MAX_GAP", "200"))
POLL_SEC = float(os.getenv("SHADOW_DUAL_POLL_SEC", "4"))
WS_TICK_SEC = float(os.getenv("SHADOW_WS_TICK_MS", "250")) / 1000.0
GAMMA_POLL_SEC = float(os.getenv("SHADOW_GAMMA_POLL_SEC", "10"))
FEED_MODE = os.getenv("SHADOW_FEED_MODE", "rest").strip().lower()
PHASE_ID = int(os.getenv("SHADOW_PHASE_ID", "1"))
PHASE_MARKER_PATH = LOG_DIR / "shadow_dual_phase_marker.json"
COMPARE_REPORT_PATH = LOG_DIR / "shadow_dual_compare_report.txt"
CSV_FIELDS = [
    "closed_at", "strategy", "slug", "yes_shares", "no_shares", "yes_avg", "no_avg",
    "combined_avg", "matched", "total_cost", "proceeds", "pnl_usdc", "cum_pnl",
    "trades", "exit_reason", "feed_mode", "phase",
]
MAX_BATCH = float(os.getenv("SHADOW_MAX_BATCH", "20"))
BATCH_A = min(float(os.getenv("SHADOW_A_BATCH", "20")), MAX_BATCH)
BATCH_B = min(float(os.getenv("SHADOW_B_BATCH", "20")), MAX_BATCH)
TARGET_COMBINED = float(os.getenv("SHADOW_TARGET_COMBINED", "0.95"))
ENDGAME_MAX_COMBINED = float(os.getenv("SHADOW_ENDGAME_MAX_COMBINED", "0.98"))
MAX_HEDGE_ASK = float(os.getenv("SHADOW_MAX_HEDGE_ASK", "0.85"))
LARGE_GAP = float(os.getenv("SHADOW_LARGE_GAP", "50"))
CHEAP_MAX = 0.45
MM_MAX_BUY = 0.89
MM_NO_BUY_HEAVY = 0.90
MM_EXPOSURE_CHEAP = 0.25
B_EXPENSIVE_LEG = float(os.getenv("SHADOW_B_EXPENSIVE_LEG", "0.50"))
B_CHEAP_DILUTE = float(os.getenv("SHADOW_B_CHEAP_DILUTE", "0.45"))
BOOK_TAKE_RATIO = float(os.getenv("SHADOW_BOOK_TAKE_RATIO", "1.0"))
SLIPPAGE_PCT = float(os.getenv("SHADOW_SLIPPAGE_PCT", "0.0"))
ENDGAME_SEC = 60
MIN_LEG_USDC = 1.0


@dataclass
class Position:
    slug: str = ""
    yes_sh: float = 0.0
    no_sh: float = 0.0
    yes_cost: float = 0.0
    no_cost: float = 0.0
    spent: float = 0.0
    fees: float = 0.0
    trades: list[dict] = field(default_factory=list)

    def yes_avg(self) -> float:
        return self.yes_cost / self.yes_sh if self.yes_sh > 0 else 0.0

    def no_avg(self) -> float:
        return self.no_cost / self.no_sh if self.no_sh > 0 else 0.0

    def gap(self) -> float:
        return abs(self.yes_sh - self.no_sh)

    def matched(self) -> float:
        return min(self.yes_sh, self.no_sh)

    def combined_avg(self) -> float:
        ya, na = self.yes_avg(), self.no_avg()
        if ya > 0 and na > 0:
            return ya + na
        if ya > 0:
            return ya
        if na > 0:
            return na
        return 0.0

    def heavy_side(self) -> Side | None:
        if self.yes_sh > self.no_sh + 1e-6:
            return "yes"
        if self.no_sh > self.yes_sh + 1e-6:
            return "no"
        return None


@dataclass
class StrategyBook:
    name: str
    budget: float
    pos: Position = field(default_factory=Position)
    cum_pnl: float = 0.0
    csv_path: Path = field(default_factory=Path)
    endgame_heavy_was_winning: bool = False

    def cash_left(self) -> float:
        return max(0.0, self.budget - self.pos.spent)

    def can_spend(self, amount: float, emergency: float = 0.0) -> bool:
        return self.pos.spent + amount <= self.budget + emergency + 1e-6


def dynamic_batch(cash: float, price: float, gap: float | None = None, scale: float = 1.0) -> float:
    """Batch size capped at MAX_BATCH (20), scaled by cash and optional gap."""
    if price <= 0 or cash < MIN_LEG_USDC:
        return 0.0
    afford = cash / (price * (1 + FEE_RATE))
    gap_lim = MAX_BATCH if gap is None else min(MAX_BATCH, gap)
    raw = min(MAX_BATCH, gap_lim, afford) * scale
    if gap is not None and gap >= LARGE_GAP:
        raw = min(raw, max(10.0, MAX_BATCH * 0.5))
    return max(0.0, min(raw, MAX_BATCH))


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with (LOG_DIR / "shadow_dual.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def probe_btc_5m() -> dict | None:
    now = time.time()
    base = int(now // 300) * 300
    for ts in (base, base + 300, base - 300):
        slug = f"btc-updown-5m-{ts}"
        try:
            res = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=12)
            res.raise_for_status()
            events = res.json()
            if not events:
                continue
            markets = events[0].get("markets") or []
            if not markets:
                continue
            m = markets[0]
            tokens = m.get("clobTokenIds") or m.get("clob_token_ids")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            end_raw = m.get("endDate") or events[0].get("endDate")
            end_ts = _parse_ts(end_raw)
            if end_ts <= now:
                continue
            return {
                "slug": slug,
                "yes_token": str(tokens[0]),
                "no_token": str(tokens[1]),
                "end_ts": end_ts,
            }
        except Exception:
            continue
    return None


def _parse_ts(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    if s.isdigit():
        return float(s)
    from datetime import datetime as dt

    try:
        return dt.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def fetch_ask_ladder(token_id: str) -> list[tuple[float, float]]:
    try:
        res = requests.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=12)
        res.raise_for_status()
        asks = res.json().get("asks") or []
        levels: list[tuple[float, float]] = []
        for a in asks:
            px = float(a.get("price", 0))
            sz = float(a.get("size", 0))
            if px > 0 and sz > 0:
                levels.append((px, sz))
        levels.sort(key=lambda x: x[0])
        return levels
    except Exception:
        return []


def walk_ask_fill(
    ladder: list[tuple[float, float]],
    max_shares: float,
) -> tuple[float, float, float]:
    """Walk ask ladder: returns (filled_shares, avg_price, best_ask)."""
    if max_shares <= 0 or not ladder:
        return 0.0, 0.0, 0.0
    best = ladder[0][0]
    remaining = max_shares
    total_cost = 0.0
    filled = 0.0
    take_ratio = max(0.05, min(1.0, BOOK_TAKE_RATIO))
    for px, sz in ladder:
        eff_px = px * (1.0 + SLIPPAGE_PCT)
        avail = sz * take_ratio
        take = min(remaining, avail)
        if take <= 0:
            continue
        total_cost += take * eff_px
        filled += take
        remaining -= take
        if remaining <= 1e-6:
            break
    avg = total_cost / filled if filled > 0 else 0.0
    return filled, avg, best


def ladder_best_ask(ladder: list[tuple[float, float]]) -> float:
    return ladder[0][0] if ladder else 0.0


def ladder_depth_near_best(ladder: list[tuple[float, float]], pct: float = 0.02) -> float:
    if not ladder:
        return 0.0
    best = ladder[0][0]
    return sum(sz for px, sz in ladder if px <= best * (1.0 + pct) + 1e-9)


def buy(
    book: StrategyBook,
    side: Side,
    want_shares: float,
    ladder: list[tuple[float, float]],
    yes_ask: float,
    no_ask: float,
    note: str,
) -> bool:
    filled, price, best = walk_ask_fill(ladder, want_shares)
    if filled <= 0 or price <= 0:
        if want_shares * best >= MIN_LEG_USDC * 0.5:
            depth = ladder_depth_near_best(ladder)
            log(
                f"[{book.name}] NO-FILL {side.upper()} want={want_shares:.1f}sh "
                f"depth@best={depth:.1f} — 吃不满"
            )
        return False
    shares = filled
    cost = shares * price
    fee = cost * FEE_RATE
    total = cost + fee
    if total < MIN_LEG_USDC:
        return False
    if not book.can_spend(total):
        return False
    p = book.pos
    if side == "yes":
        p.yes_sh += shares
        p.yes_cost += cost
    else:
        p.no_sh += shares
        p.no_cost += cost
    p.spent += total
    p.fees += fee
    fill_tag = ""
    if shares < want_shares - 0.5:
        fill_tag = f" | fill {shares:.1f}/{want_shares:.1f}sh"
    if price > best * 1.001:
        fill_tag += f" slip avg={price:.4f} best={best:.4f}"
    p.trades.append(
        {"side": side, "shares": shares, "price": price, "cost": cost, "note": note + fill_tag}
    )
    comb = p.combined_avg()
    log(
        f"[{book.name}] BUY {side.upper()} {shares:.1f}sh @ {price:.4f} "
        f"(${cost:.2f}) | {note}{fill_tag} | gap={p.gap():.1f} comb={comb:.4f} "
        f"cash_left=${book.cash_left():.2f}"
    )
    return True


def projected_combined_after_hedge(p: Position, light: Side, light_ask: float, fill: float) -> float:
    if light == "yes":
        new_yes = (p.yes_cost + fill * light_ask) / (p.yes_sh + fill)
        new_no = p.no_avg() if p.no_sh > 0 else 0.0
    else:
        new_no = (p.no_cost + fill * light_ask) / (p.no_sh + fill)
        new_yes = p.yes_avg() if p.yes_sh > 0 else 0.0
    if new_yes > 0 and new_no > 0:
        return new_yes + new_no
    return new_yes + new_no


def hedge_economics(
    gap: float, heavy_avg: float, light_ask: float, fill: float
) -> dict[str, float | str | bool]:
    """Compare locking matched `fill` shares vs leaving naked if heavy side loses."""
    combined = heavy_avg + light_ask
    light_spend = fill * light_ask * (1 + FEE_RATE)
    naked_loss_if_loses = fill * heavy_avg
    locked_pnl = fill * (1.0 - combined) - fill * light_ask * FEE_RATE
    heavy_win_gain = fill * (1.0 - heavy_avg)
    reason = ""
    worth = False
    if combined > ENDGAME_MAX_COMBINED + 1e-9:
        reason = f"comb {combined:.4f}>{ENDGAME_MAX_COMBINED} skip"
    elif locked_pnl > 0.05:
        worth = True
        reason = f"lock +${locked_pnl:.2f}"
    elif gap >= LARGE_GAP and heavy_avg <= MM_EXPOSURE_CHEAP + 0.05:
        if locked_pnl > -naked_loss_if_loses * 0.25:
            worth = True
            reason = (
                f"gap={gap:.0f} cheap-exp hedge ${locked_pnl:+.2f} "
                f"vs naked-lose -${naked_loss_if_loses:.2f}"
            )
        else:
            reason = (
                f"gap={gap:.0f} hedge ${locked_pnl:+.2f} worse than "
                f"accept naked -${naked_loss_if_loses:.2f}"
            )
    elif gap >= LARGE_GAP and locked_pnl >= -fill * 0.005:
        worth = True
        reason = f"gap={gap:.0f} small-loss hedge ${locked_pnl:+.2f}"
    elif gap < LARGE_GAP and locked_pnl > -fill * 0.01:
        worth = combined <= ENDGAME_MAX_COMBINED
        reason = f"gap={gap:.0f} hedge ${locked_pnl:+.2f} (small gap)"
    else:
        reason = f"skip hedge ${locked_pnl:+.2f} naked-risk -${naked_loss_if_loses:.2f}"
    return {
        "combined": combined,
        "fill": fill,
        "light_spend": light_spend,
        "locked_pnl": locked_pnl,
        "naked_loss_if_loses": naked_loss_if_loses,
        "heavy_win_gain": heavy_win_gain,
        "worth": worth,
        "reason": reason,
    }


def projected_combined_after_buy(p: Position, side: Side, ask: float, fill: float) -> float:
    if fill <= 0 or ask <= 0:
        return p.combined_avg()
    if side == "yes":
        new_yes = (p.yes_cost + fill * ask) / (p.yes_sh + fill)
        new_no = p.no_avg() if p.no_sh > 0 else 0.0
    else:
        new_no = (p.no_cost + fill * ask) / (p.no_sh + fill)
        new_yes = p.yes_avg() if p.yes_sh > 0 else 0.0
    if new_yes > 0 and new_no > 0:
        return new_yes + new_no
    return new_yes + new_no


def try_hedge_light(
    book: StrategyBook,
    yes_ask: float,
    no_ask: float,
    yes_ladder: list[tuple[float, float]],
    no_ladder: list[tuple[float, float]],
    max_fill: float,
    max_light_ask: float,
    max_combined: float,
    note: str,
    emergency: float = 0.0,
    require_economics: bool = False,
) -> bool:
    p = book.pos
    heavy = p.heavy_side()
    if heavy is None or p.gap() <= 0:
        return False
    light: Side = "no" if heavy == "yes" else "yes"
    light_ask = no_ask if light == "no" else yes_ask
    heavy_avg = p.yes_avg() if heavy == "yes" else p.no_avg()
    if light_ask <= 0 or heavy_avg <= 0:
        return False
    if light_ask > max_light_ask + 1e-9:
        return False
    fill = min(max_fill, p.gap())
    marginal = heavy_avg + light_ask
    if marginal > max_combined + 1e-9:
        return False
    proj = projected_combined_after_hedge(p, light, light_ask, fill)
    if proj > max_combined + 1e-9:
        return False
    if require_economics:
        econ = hedge_economics(p.gap(), heavy_avg, light_ask, fill)
        if not econ["worth"]:
            log(f"[{book.name}] SKIP-HEDGE {econ['reason']}")
            return False
        note = f"{note} | {econ['reason']}"
    if fill * light_ask * (1 + FEE_RATE) > book.cash_left() + emergency + 1e-6:
        fill = (book.cash_left() + emergency) / (light_ask * (1 + FEE_RATE))
    fill = min(fill, max_fill, p.gap())
    if fill <= 0:
        return False
    if not book.can_spend(fill * light_ask * (1 + FEE_RATE), emergency):
        return False
    ladder = yes_ladder if light == "yes" else no_ladder
    return buy(
        book, light, fill, ladder, yes_ask, no_ask,
        f"{note} marg={marginal:.4f} proj={proj:.4f}",
    )


def a_max_entry_shares(p: Position, side: Side, want: float) -> float:
    """Cap A cheap-leg buys so unhedged gap stays <= A_MAX_GAP."""
    if want <= 0:
        return 0.0
    gap = p.gap()
    if gap >= A_MAX_GAP:
        heavy = p.heavy_side()
        if heavy is None or side == heavy:
            return 0.0
        return want
    if p.yes_sh <= 0 and p.no_sh <= 0:
        return min(want, A_MAX_GAP)
    heavy = p.heavy_side()
    if heavy is None:
        return min(want, A_MAX_GAP)
    if side == heavy:
        return min(want, max(0.0, A_MAX_GAP - gap))
    return want


def strat_a_tick(
    book: StrategyBook,
    yes_ask: float,
    no_ask: float,
    yes_ladder: list[tuple[float, float]],
    no_ladder: list[tuple[float, float]],
    secs_left: float,
    reserve: float,
) -> None:
    p = book.pos
    in_endgame = secs_left <= ENDGAME_SEC
    deployable = max(0.0, book.budget - reserve)

    if in_endgame and p.gap() >= BATCH_A * 0.5:
        try_hedge_light(
            book, yes_ask, no_ask, yes_ladder, no_ladder, p.gap(), MAX_HEDGE_ASK, TARGET_COMBINED,
            "A-endgame-hedge", emergency=reserve * 0.5,
        )
        if secs_left <= 30 and p.gap() >= 10:
            try_hedge_light(
                book, yes_ask, no_ask, yes_ladder, no_ladder,
                min(p.gap(), BATCH_A * 2), ENDGAME_MAX_COMBINED,
                ENDGAME_MAX_COMBINED, "A-late-balance", emergency=reserve,
                require_economics=True,
            )
        return

    if p.yes_sh > 0 or p.no_sh > 0:
        heavy = p.heavy_side()
        if heavy and p.gap() >= 5:
            light: Side = "no" if heavy == "yes" else "yes"
            light_ask = no_ask if light == "no" else yes_ask
            heavy_avg = p.yes_avg() if heavy == "yes" else p.no_avg()
            if heavy_avg > 0 and light_ask > 0 and heavy_avg + light_ask <= TARGET_COMBINED + 1e-9:
                fill_a = dynamic_batch(book.cash_left(), light_ask, p.gap())
                if fill_a > 0:
                    try_hedge_light(
                        book, yes_ask, no_ask, yes_ladder, no_ladder,
                        fill_a, light_ask + 1e-9,
                        TARGET_COMBINED, "A-hedge",
                    )

    cheap: list[tuple[Side, float]] = []
    if yes_ask > 0 and yes_ask <= CHEAP_MAX:
        cheap.append(("yes", yes_ask))
    if no_ask > 0 and no_ask <= CHEAP_MAX:
        cheap.append(("no", no_ask))
    if not cheap:
        return
    if book.pos.spent >= deployable - MIN_LEG_USDC:
        return
    side, px = min(cheap, key=lambda x: x[1])
    cash = min(book.cash_left(), deployable - book.pos.spent)
    if cash < MIN_LEG_USDC:
        return
    shares = dynamic_batch(cash, px, scale=1.0)
    if shares * px < MIN_LEG_USDC:
        return
    shares = a_max_entry_shares(p, side, shares)
    if shares * px < MIN_LEG_USDC:
        return
    ladder = yes_ladder if side == "yes" else no_ladder
    buy(book, side, shares, ladder, yes_ask, no_ask, "A-cheap-leg")


def b_gradual_hedge_batch(
    book: StrategyBook,
    yes_ask: float,
    no_ask: float,
    yes_ladder: list[tuple[float, float]],
    no_ladder: list[tuple[float, float]],
    light: Side,
    light_ask: float,
    heavy_avg: float,
    note: str,
) -> None:
    """One small hedge batch; never full gap at once."""
    p = book.pos
    fill = dynamic_batch(book.cash_left(), light_ask, p.gap(), scale=0.75 if p.gap() >= LARGE_GAP else 1.0)
    if fill <= 0:
        return
    proj = projected_combined_after_hedge(p, light, light_ask, fill)
    if proj > ENDGAME_MAX_COMBINED + 1e-9:
        smaller = fill
        while smaller > 5 and projected_combined_after_hedge(p, light, light_ask, smaller) > ENDGAME_MAX_COMBINED:
            smaller -= 5
        fill = smaller if smaller >= 5 else 0
    if fill <= 0:
        log(f"[{book.name}] SKIP batch — proj comb > {ENDGAME_MAX_COMBINED} @ light {light_ask:.4f}")
        return

    if p.gap() >= LARGE_GAP or heavy_avg <= MM_EXPOSURE_CHEAP + 0.05:
        econ = hedge_economics(p.gap(), heavy_avg, light_ask, fill)
        log(
            f"[{book.name}] ECON gap={p.gap():.0f} heavy_avg={heavy_avg:.4f} "
            f"comb={float(econ['combined']):.4f} lock=${float(econ['locked_pnl']):+.2f} "
            f"naked=-${float(econ['naked_loss_if_loses']):.2f} | {econ['reason']}"
        )
        if not econ["worth"]:
            return

    try_hedge_light(
        book, yes_ask, no_ask, yes_ladder, no_ladder, fill, light_ask + 1e-9,
        ENDGAME_MAX_COMBINED, note,
    )


def strat_b_endgame_hedge(
    book: StrategyBook,
    yes_ask: float,
    no_ask: float,
    yes_ladder: list[tuple[float, float]],
    no_ladder: list[tuple[float, float]],
    heavy_ask: float,
    secs_left: float,
) -> None:
    """Last minute dynamic endgame.

    - Heavy ask > 0.9: winning side — hold, do NOT hedge; wait for price to drop.
    - Heavy ask <= 0.25: cheap exposure — gradual opposite batches (anti-reversal).
    - Between: hedge only after heavy was winning and dropped, or if combined <= target.
    """
    p = book.pos
    heavy = p.heavy_side()
    if heavy is None or p.gap() < 5:
        return
    light: Side = "no" if heavy == "yes" else "yes"
    light_ask = no_ask if light == "no" else yes_ask
    heavy_avg = p.yes_avg() if heavy == "yes" else p.no_avg()
    if light_ask <= 0 or heavy_avg <= 0:
        return

    heavy_label = heavy.upper()

    if heavy_ask > MM_NO_BUY_HEAVY:
        book.endgame_heavy_was_winning = True
        log(
            f"[{book.name}] HOLD endgame | {heavy_label} ask={heavy_ask:.4f}>0.90 "
            f"gap={p.gap():.0f} — winning, wait for drop before hedge"
        )
        return

    if heavy_ask <= MM_EXPOSURE_CHEAP:
        log(
            f"[{book.name}] GRADUAL-HEDGE | {heavy_label} ask={heavy_ask:.4f}<=0.25 "
            f"gap={p.gap():.0f} — cheap exposure, batch buy {light.upper()}"
        )
        b_gradual_hedge_batch(
            book, yes_ask, no_ask, yes_ladder, no_ladder, light, light_ask, heavy_avg,
            "B-cheap-exp batch",
        )
        return

    marginal = heavy_avg + light_ask
    dropped_from_win = book.endgame_heavy_was_winning and heavy_ask < MM_NO_BUY_HEAVY
    if dropped_from_win:
        log(
            f"[{book.name}] DYNAMIC-HEDGE | {heavy_label} dropped to {heavy_ask:.4f} "
            f"(was >0.90) gap={p.gap():.0f} marg={marginal:.4f}"
        )
        if marginal <= ENDGAME_MAX_COMBINED + 1e-9:
            b_gradual_hedge_batch(
                book, yes_ask, no_ask, yes_ladder, no_ladder, light, light_ask, heavy_avg,
                "B-after-drop",
            )
        return

    if marginal <= TARGET_COMBINED + 1e-9:
        b_gradual_hedge_batch(
            book, yes_ask, no_ask, yes_ladder, no_ladder, light, light_ask, heavy_avg,
            "B-marginal-ok",
        )
        return

    if secs_left <= 15 and p.gap() >= LARGE_GAP and marginal <= ENDGAME_MAX_COMBINED + 1e-9:
        b_gradual_hedge_batch(
            book, yes_ask, no_ask, yes_ladder, no_ladder, light, light_ask, heavy_avg,
            "B-late-large-gap",
        )


def b_entry_allowed(p: Position, side: Side, ask: float, yes_ask: float, no_ask: float) -> bool:
    """B mid-round: avoid expensive-leg DCA when combined already high."""
    comb = p.combined_avg()
    if p.matched() <= 0 and (yes_ask + no_ask) > TARGET_COMBINED + 0.02:
        if ask >= max(yes_ask, no_ask) - 1e-9:
            return False

    if p.yes_sh > 0 and p.no_sh > 0:
        if comb > TARGET_COMBINED + 1e-9:
            return ask < B_CHEAP_DILUTE
        if comb > TARGET_COMBINED - 0.03:
            return ask < B_EXPENSIVE_LEG

    if side == "yes" and p.yes_sh > 0 and p.no_sh <= 0:
        if p.yes_avg() + ask > TARGET_COMBINED + 1e-9:
            return False
    if side == "no" and p.no_sh > 0 and p.yes_sh <= 0:
        if p.no_avg() + ask > TARGET_COMBINED + 1e-9:
            return False

    if p.matched() > 0 and comb <= TARGET_COMBINED - 0.02 and ask > 0.55:
        return False

    return True


def strat_b_tick(
    book: StrategyBook,
    yes_ask: float,
    no_ask: float,
    yes_ladder: list[tuple[float, float]],
    no_ladder: list[tuple[float, float]],
    secs_left: float,
    reserve: float,
) -> None:
    p = book.pos
    in_endgame = secs_left <= ENDGAME_SEC

    if in_endgame:
        heavy = p.heavy_side()
        if heavy is None:
            return
        heavy_ask = yes_ask if heavy == "yes" else no_ask
        strat_b_endgame_hedge(
            book, yes_ask, no_ask, yes_ladder, no_ladder, heavy_ask, secs_left,
        )
        return

    for side, ask, ladder in (
        ("yes", yes_ask, yes_ladder),
        ("no", no_ask, no_ladder),
    ):
        if ask <= 0 or ask > MM_MAX_BUY:
            continue
        if not b_entry_allowed(p, side, ask, yes_ask, no_ask):
            continue
        if book.cash_left() < MIN_LEG_USDC:
            continue
        shares = dynamic_batch(book.cash_left(), ask, scale=1.0)
        if shares * ask < MIN_LEG_USDC:
            continue
        proj = projected_combined_after_buy(p, side, ask, shares)
        if proj > TARGET_COMBINED + 1e-9 and p.matched() > 0:
            continue
        buy(book, side, shares, ladder, yes_ask, no_ask, "B-mm-dca")


def settle_round(
    book: StrategyBook,
    yes_ask: float,
    no_ask: float,
    feed_mode: str = "rest_4s",
    phase: int = 1,
) -> None:
    p = book.pos
    if p.yes_sh <= 0 and p.no_sh <= 0:
        return
    yes_wins = yes_ask >= no_ask and yes_ask > 0.55
    yes_exit = 1.0 if yes_wins else 0.0
    no_exit = 1.0 - yes_exit
    proceeds = p.yes_sh * yes_exit + p.no_sh * no_exit
    exit_fee = proceeds * FEE_RATE
    proceeds -= exit_fee
    total_cost = p.yes_cost + p.no_cost + p.fees
    pnl = proceeds - total_cost
    book.cum_pnl += pnl
    row = {
        "closed_at": f"{time.time():.3f}",
        "strategy": book.name,
        "slug": p.slug,
        "yes_shares": f"{p.yes_sh:.4f}",
        "no_shares": f"{p.no_sh:.4f}",
        "yes_avg": f"{p.yes_avg():.4f}",
        "no_avg": f"{p.no_avg():.4f}",
        "combined_avg": f"{p.combined_avg():.4f}",
        "matched": f"{p.matched():.4f}",
        "total_cost": f"{total_cost:.4f}",
        "proceeds": f"{proceeds:.4f}",
        "pnl_usdc": f"{pnl:.4f}",
        "cum_pnl": f"{book.cum_pnl:.4f}",
        "trades": str(len(p.trades)),
        "exit_reason": "resolved",
        "feed_mode": feed_mode,
        "phase": str(phase),
    }
    write_csv_row(book.csv_path, row)
    log(
        f"[{book.name}] CLOSED {p.slug} | comb={p.combined_avg():.4f} "
        f"PnL ${pnl:+.2f} cum ${book.cum_pnl:+.2f} trades={len(p.trades)} "
        f"feed={feed_mode} phase={phase}"
    )
    book.pos = Position()


def write_csv_row(path: Path, row: dict) -> None:
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def summarize_csv_phase(
    path: Path,
    t_start: float,
    t_end: float,
    feed_mode: str | None = None,
    phase: int | None = None,
) -> dict:
    if not path.exists():
        return {"rounds": 0, "pnl_sum": 0.0, "win_rate": 0.0, "median_comb": 0.0}
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = float(row.get("closed_at") or 0)
            except ValueError:
                continue
            if ts < t_start or ts >= t_end:
                continue
            if feed_mode and row.get("feed_mode") and row["feed_mode"] != feed_mode:
                continue
            if phase is not None and row.get("phase") and int(row["phase"]) != phase:
                continue
            rows.append(row)
    if not rows:
        return {"rounds": 0, "pnl_sum": 0.0, "win_rate": 0.0, "median_comb": 0.0}
    pnls = [float(r["pnl_usdc"]) for r in rows]
    combs = sorted(float(r["combined_avg"]) for r in rows if float(r.get("matched") or 0) > 0)
    wins = sum(1 for p in pnls if p > 0)
    med = combs[len(combs) // 2] if combs else 0.0
    return {
        "rounds": len(rows),
        "pnl_sum": sum(pnls),
        "win_rate": wins / len(rows),
        "median_comb": med,
    }


def write_phase_marker(data: dict) -> None:
    PHASE_MARKER_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_phase_marker() -> dict:
    if not PHASE_MARKER_PATH.exists():
        return {}
    return json.loads(PHASE_MARKER_PATH.read_text(encoding="utf-8"))


def compare_and_report(marker: dict) -> str:
    p1 = marker.get("phase1", {})
    p2 = marker.get("phase2", {})
    t1s, t1e = float(p1.get("start_ts", 0)), float(p1.get("end_ts", 0))
    t2s, t2e = float(p2.get("start_ts", 0)), float(p2.get("end_ts", time.time()))
    a1 = summarize_csv_phase(LOG_DIR / "shadow_dual_a.csv", t1s, t1e)
    b1 = summarize_csv_phase(LOG_DIR / "shadow_dual_b.csv", t1s, t1e)
    a2 = summarize_csv_phase(
        LOG_DIR / "shadow_dual_a.csv", t2s, t2e, feed_mode="ws_clob", phase=2,
    )
    b2 = summarize_csv_phase(
        LOG_DIR / "shadow_dual_b.csv", t2s, t2e, feed_mode="ws_clob", phase=2,
    )
    lines = [
        "=" * 60,
        "Shadow Dual A/B — Phase Compare",
        "=" * 60,
        "",
        f"Phase 1 REST poll ({POLL_SEC}s): {p1.get('start_utc','')} → {p1.get('end_utc','')}",
        f"  A: rounds={a1['rounds']} PnL=${a1['pnl_sum']:+.2f} win={a1['win_rate']*100:.0f}% med_comb={a1['median_comb']:.3f}",
        f"  B: rounds={b1['rounds']} PnL=${b1['pnl_sum']:+.2f} win={b1['win_rate']*100:.0f}% med_comb={b1['median_comb']:.3f}",
        "",
        f"Phase 2 CLOB WS (~{WS_TICK_SEC*1000:.0f}ms tick): {p2.get('start_utc','')} → {p2.get('end_utc','')}",
        f"  A: rounds={a2['rounds']} PnL=${a2['pnl_sum']:+.2f} win={a2['win_rate']*100:.0f}% med_comb={a2['median_comb']:.3f}",
        f"  B: rounds={b2['rounds']} PnL=${b2['pnl_sum']:+.2f} win={b2['win_rate']*100:.0f}% med_comb={b2['median_comb']:.3f}",
        "",
        "Delta (Phase2 − Phase1) PnL:",
        f"  A: ${a2['pnl_sum'] - a1['pnl_sum']:+.2f}",
        f"  B: ${b2['pnl_sum'] - b1['pnl_sum']:+.2f}",
        "=" * 60,
    ]
    report = "\n".join(lines)
    COMPARE_REPORT_PATH.write_text(report + "\n", encoding="utf-8")
    return report


def process_round_tick(
    strat_a: StrategyBook,
    strat_b: StrategyBook,
    mkt: dict,
    yes_ladder: list[tuple[float, float]],
    no_ladder: list[tuple[float, float]],
    yes_ask: float,
    no_ask: float,
    secs_left: float,
    last_slug: str,
    feed_mode: str,
    phase: int,
) -> str:
    slug = mkt["slug"]
    if last_slug and slug != last_slug:
        settle_round(strat_a, yes_ask, no_ask, feed_mode, phase)
        settle_round(strat_b, yes_ask, no_ask, feed_mode, phase)

    if slug != last_slug:
        strat_a.pos = Position(slug=slug)
        strat_b.pos = Position(slug=slug)
        strat_b.endgame_heavy_was_winning = False
        last_slug = slug
        log(
            f"--- new round {slug} | book YES={yes_ask:.4f} NO={no_ask:.4f} "
            f"sum={yes_ask+no_ask:.4f} feed={feed_mode} ---"
        )

    if yes_ask <= 0 or no_ask <= 0:
        return last_slug

    if secs_left > 10:
        strat_a_tick(strat_a, yes_ask, no_ask, yes_ladder, no_ladder, secs_left, A_RESERVE)
        strat_b_tick(strat_b, yes_ask, no_ask, yes_ladder, no_ladder, secs_left, 0.0)
    elif secs_left > 0:
        strat_a_tick(strat_a, yes_ask, no_ask, yes_ladder, no_ladder, secs_left, A_RESERVE)
        strat_b_tick(strat_b, yes_ask, no_ask, yes_ladder, no_ladder, secs_left, 0.0)
    else:
        settle_round(strat_a, yes_ask, no_ask, feed_mode, phase)
        settle_round(strat_b, yes_ask, no_ask, feed_mode, phase)
        last_slug = ""
    return last_slug


def run_loop_rest(duration_sec: float = 0, feed_mode: str = "rest_4s", phase: int = 1) -> int:
    load_dotenv(ROOT / ".env")
    strat_a = StrategyBook(
        "cheap_sweep",
        STRAT_BUDGET,
        csv_path=LOG_DIR / "shadow_dual_a.csv",
    )
    strat_b = StrategyBook(
        "mm_dca",
        STRAT_BUDGET,
        csv_path=LOG_DIR / "shadow_dual_b.csv",
    )

    log(
        f"=== dual shadow start feed={feed_mode} phase={phase} poll={POLL_SEC}s | "
        f"total=${TOTAL_BANKROLL:.0f} "
        f"A=${STRAT_BUDGET:.0f} (reserve ${A_RESERVE:.0f} gap_cap={A_MAX_GAP:.0f}) B=${STRAT_BUDGET:.0f} "
        f"profit-target<{TARGET_COMBINED} endgame-cap<={ENDGAME_MAX_COMBINED} "
        f"book_take={BOOK_TAKE_RATIO} slip={SLIPPAGE_PCT:.3f} ==="
    )

    start = time.time()
    last_slug = ""
    yes_ask = no_ask = 0.0

    while True:
        if duration_sec > 0 and time.time() - start >= duration_sec:
            log("=== duration reached, stopping ===")
            break

        mkt = probe_btc_5m()
        if not mkt:
            time.sleep(POLL_SEC)
            continue

        yes_ladder = fetch_ask_ladder(mkt["yes_token"])
        no_ladder = fetch_ask_ladder(mkt["no_token"])
        yes_ask = ladder_best_ask(yes_ladder)
        no_ask = ladder_best_ask(no_ladder)
        secs_left = mkt["end_ts"] - time.time()

        last_slug = process_round_tick(
            strat_a, strat_b, mkt, yes_ladder, no_ladder,
            yes_ask, no_ask, secs_left, last_slug, feed_mode, phase,
        )
        time.sleep(POLL_SEC)

    if last_slug:
        settle_round(strat_a, yes_ask, no_ask, feed_mode, phase)
        settle_round(strat_b, yes_ask, no_ask, feed_mode, phase)

    log(f"=== done feed={feed_mode} A cum=${strat_a.cum_pnl:+.2f} B cum=${strat_b.cum_pnl:+.2f} ===")
    return 0


def run_loop_ws(duration_sec: float = 0, phase: int = 2) -> int:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from shadow_clob_ws_feed import ClobWSBookFeed

    load_dotenv(ROOT / ".env")
    feed_mode = "ws_clob"
    strat_a = StrategyBook("cheap_sweep", STRAT_BUDGET, csv_path=LOG_DIR / "shadow_dual_a.csv")
    strat_b = StrategyBook("mm_dca", STRAT_BUDGET, csv_path=LOG_DIR / "shadow_dual_b.csv")

    book_dirty = threading.Event()
    ws_feed = ClobWSBookFeed(on_book_update=lambda _tid: book_dirty.set())
    ws_feed.start()

    log(
        f"=== dual shadow WS start feed={feed_mode} phase={phase} tick≈{WS_TICK_SEC*1000:.0f}ms | "
        f"A gap_cap={A_MAX_GAP:.0f} B endgame<={ENDGAME_MAX_COMBINED} ==="
    )

    start = time.time()
    last_slug = ""
    yes_ask = no_ask = 0.0
    mkt: dict | None = None
    last_gamma = 0.0
    last_tick = 0.0
    sub_tokens: tuple[str, str] = ("", "")

    while True:
        if duration_sec > 0 and time.time() - start >= duration_sec:
            log("=== duration reached, stopping ===")
            break

        now = time.time()
        if now - last_gamma >= GAMMA_POLL_SEC or mkt is None:
            mkt = probe_btc_5m()
            last_gamma = now
            if mkt:
                pair = (mkt["yes_token"], mkt["no_token"])
                if pair != sub_tokens:
                    ws_feed.subscribe(list(pair))
                    yl = fetch_ask_ladder(mkt["yes_token"])
                    nl = fetch_ask_ladder(mkt["no_token"])
                    ws_feed.seed_ladder(mkt["yes_token"], yl)
                    ws_feed.seed_ladder(mkt["no_token"], nl)
                    sub_tokens = pair
                    book_dirty.set()

        if not mkt:
            time.sleep(0.5)
            continue

        wait_for = max(0.01, WS_TICK_SEC - (time.time() - last_tick))
        book_dirty.wait(timeout=wait_for)
        book_dirty.clear()
        tick_now = time.time()
        if tick_now - last_tick < WS_TICK_SEC * 0.95:
            continue
        last_tick = tick_now

        yes_ladder = ws_feed.get_ask_ladder(mkt["yes_token"])
        no_ladder = ws_feed.get_ask_ladder(mkt["no_token"])
        if not yes_ladder or not no_ladder:
            yes_ladder = fetch_ask_ladder(mkt["yes_token"])
            no_ladder = fetch_ask_ladder(mkt["no_token"])
            ws_feed.seed_ladder(mkt["yes_token"], yes_ladder)
            ws_feed.seed_ladder(mkt["no_token"], no_ladder)
        yes_ask = ladder_best_ask(yes_ladder)
        no_ask = ladder_best_ask(no_ladder)
        secs_left = mkt["end_ts"] - time.time()

        last_slug = process_round_tick(
            strat_a, strat_b, mkt, yes_ladder, no_ladder,
            yes_ask, no_ask, secs_left, last_slug, feed_mode, phase,
        )

    ws_feed.stop()
    if last_slug and mkt:
        yes_ask = ws_feed.get_best_ask(mkt["yes_token"]) or yes_ask
        no_ask = ws_feed.get_best_ask(mkt["no_token"]) or no_ask
        settle_round(strat_a, yes_ask, no_ask, feed_mode, phase)
        settle_round(strat_b, yes_ask, no_ask, feed_mode, phase)

    log(f"=== done feed={feed_mode} A cum=${strat_a.cum_pnl:+.2f} B cum=${strat_b.cum_pnl:+.2f} ===")
    st = ws_feed.stats()
    log(f"=== WS stats updates={st['updates']} subscribed={st['subscribed']} ===")
    return 0


def run_loop(duration_sec: float = 0, feed_mode: str | None = None, phase: int | None = None) -> int:
    mode = (feed_mode or FEED_MODE).lower()
    ph = phase if phase is not None else PHASE_ID
    if mode in ("ws", "ws_clob", "websocket"):
        return run_loop_ws(duration_sec, phase=ph)
    return run_loop_rest(duration_sec, feed_mode="rest_4s", phase=ph)


def main() -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description="Shadow dual strategies")
    parser.add_argument("duration", nargs="?", default="0", help="Run seconds (0=forever)")
    parser.add_argument("--feed", choices=["rest", "ws"], default=None, help="rest=4s poll, ws=CLOB websocket")
    parser.add_argument("--phase", type=int, default=None, help="Phase id for CSV tagging")
    parser.add_argument("--compare", action="store_true", help="Print phase compare report and exit")
    args = parser.parse_args()

    if args.compare:
        marker = load_phase_marker()
        if not marker:
            print("No phase marker found.", file=sys.stderr)
            return 1
        print(compare_and_report(marker))
        return 0

    duration = float(args.duration)
    feed = args.feed or FEED_MODE
    if feed in ("ws", "ws_clob"):
        feed = "ws"
    phase = args.phase if args.phase is not None else (2 if feed == "ws" else PHASE_ID)

    if feed == "ws":
        marker = load_phase_marker()
        now = time.time()
        p2_start = now
        if "phase2" not in marker:
            marker.setdefault("phase1", {})
            if not marker["phase1"].get("end_ts"):
                marker["phase1"]["end_ts"] = now
                marker["phase1"]["end_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            marker["phase2"] = {
                "feed": "ws_clob",
                "start_ts": p2_start,
                "start_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "tick_ms": WS_TICK_SEC * 1000,
                "planned_duration_sec": duration,
            }
            write_phase_marker(marker)
            log(
                f"=== PHASE_CUTOVER phase=2_start feed=ws_clob "
                f"duration={duration:.0f}s tick_ms={WS_TICK_SEC*1000:.0f} ==="
            )

    rc = run_loop(duration, feed_mode=feed, phase=phase)

    if feed == "ws" and duration > 0:
        marker = load_phase_marker()
        if "phase2" in marker:
            marker["phase2"]["end_ts"] = time.time()
            marker["phase2"]["end_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            write_phase_marker(marker)
        report = compare_and_report(marker)
        log(report.replace("\n", " | "))

    return rc


if __name__ == "__main__":
    sys.exit(main())
