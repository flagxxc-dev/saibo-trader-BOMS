#!/usr/bin/env python3
"""Monitor live LIH round(s). Does NOT open positions unless --enable-live is passed."""
from __future__ import annotations

import argparse
import atexit
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402

WATCH = re.compile(
    r"LIH LIVE|LEG1|HEDGE|Bridge fill|Resolve fill|entry-wait|session leg|"
    r"no cheap leg|CLOSED|HEDGE failed|invalid amounts|AUTO-REDEEM|pending|"
    r"dead/no fill|hedge skip|lock released|Bridge dead|other slot"
)
ROUND_ID_RE = re.compile(r"LIH-([a-z]+)-\d+")
LEG1_OK_RE = re.compile(
    r"LIH LIVE LEG1 (\w+) (YES|NO)|"
    r"\[LIH LIVE\] LEG1 (LIH-[a-z]+-\d+) \| (YES|NO)|"
    r"\[LIVE LIH\] LEG1 pending resolved (\w+)|"
    r"\[LIH LIVE\] LEG1 (\w+)",
    re.I,
)
HEDGE_OK_RE = re.compile(
    r"LIH LIVE HEDGE (\w+) (YES|NO)|"
    r"\[LIH LIVE\] HEDGE (LIH-[a-z]+-\d+) \| (YES|NO)|"
    r"\[LIVE LIH\] HEDGE pending resolved (\w+)|"
    r"\[LIH LIVE\] HEDGE (\w+)",
    re.I,
)
HEDGE_FAIL_RE = re.compile(
    r"dead/no fill|HEDGE failed|pending abandoned|Bridge dead order|"
    r"\[LIVE LIH\] HEDGE dead",
    re.I,
)
ASSET_HINT_RE = re.compile(
    r"(?:LEG1|HEDGE|dead/no fill|hedge skip|pending abandoned|Bridge dead order)\s+(\w+)",
    re.I,
)

POLL_SEC = 8
MAX_WAIT_SEC = 600
SUCCESS_GRACE_SEC = 8
RETRY_GRACE_SEC = 25


def ro(c, cmd, t=60):
    _, o, e = c.exec_command(cmd, timeout=t, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def set_env(c, key: str, val: str) -> None:
    ro(
        c,
        f"grep -q '^{key}=' '{PROJ}/.env' && "
        f"sed -i 's/^{key}=.*/{key}={val}/' '{PROJ}/.env' || "
        f"echo '{key}={val}' >> '{PROJ}/.env'",
    )


def api_snapshot(c) -> dict:
    raw = ro(
        c,
        "curl -s -m 8 http://127.0.0.1:8081/api/config | python3 -c \"import sys,json; "
        "l=json.load(sys.stdin).get('live',{}); "
        "print(json.dumps({'open':l.get('openCount'),'sess':l.get('lihSessionLegsUsed'),"
        "'sessMax':l.get('lihSessionMaxLegs'),'riskMax':l.get('riskMaxConcurrentPositions'),"
        "'status':l.get('status'),'reason':l.get('statusReason',''),'bal':l.get('balance'),"
        "'oneSlot':l.get('lihOneSlotGlobal'),'eth5m':l.get('dhEnable5mEth'),"
        "'ops':l.get('openPositions') or []}))\"",
        t=15,
    )
    try:
        return json.loads(raw.splitlines()[-1])
    except Exception:
        return {}


def stop_new_entries(c, reason: str) -> None:
    print(f"\n=== STOP ({reason}) ===", flush=True)
    set_env(c, "RISK_MAX_CONCURRENT_POSITIONS", "0")
    set_env(c, "LIH_PAUSE_AFTER_ROUND", "true")
    patch = json.dumps(
        {
            "patch": {
                "RISK_MAX_CONCURRENT_POSITIONS": "0",
                "LIH_PAUSE_AFTER_ROUND": "true",
            },
            "control": "pause",
            "reason": f"test-stop: {reason}",
            "user": f"stop-{reason}",
        }
    )
    ro(c, f"printf '%s' '{patch}' > '{PROJ}/logs/runtime_config.json'")
    time.sleep(4)
    snap = api_snapshot(c)
    print(
        f"riskMax={snap.get('riskMax')} open={snap.get('open')} "
        f"session={snap.get('sess')}/{snap.get('sessMax')} status={snap.get('status')} "
        f"reason={snap.get('reason')}",
        flush=True,
    )


def enable_round(c, risk_max: int, session_legs: int, expect_assets: list[str]) -> None:
    print(f"=== ENABLE LIVE TEST (riskMax={risk_max}, assets={expect_assets}) ===", flush=True)
    print(
        ro(
            c,
            f"grep -E '^(PAPER_MODE|LIVE_LIH_DRY_RUN|LIH_ENABLED|RISK_MAX|LIH_PAUSE|"
            f"DH_ENABLE_5M_ETH|LIH_ONE_SLOT)' '{PROJ}/.env'",
        ),
        flush=True,
    )
    set_env(c, "RISK_MAX_CONCURRENT_POSITIONS", str(risk_max))
    set_env(c, "LIH_ONE_SLOT_GLOBAL", "false" if risk_max > 1 else "true")
    set_env(c, "LIH_SESSION_MAX_LEGS", str(session_legs))
    set_env(c, "LIH_PAUSE_AFTER_ROUND", "true")
    if "btc" in expect_assets:
        set_env(c, "DH_ENABLE_5M_BTC", "true")
    if "eth" in expect_assets:
        set_env(c, "DH_ENABLE_5M_ETH", "true")
    patch = {
        "patch": {
            "RISK_MAX_CONCURRENT_POSITIONS": str(risk_max),
            "LIH_PAUSE_AFTER_ROUND": "true",
            "LIH_ONE_SLOT_GLOBAL": "false" if risk_max > 1 else "true",
            "LIH_SESSION_MAX_LEGS": str(session_legs),
        },
        "control": "resume",
        "user": "live-test-round",
    }
    if "btc" in expect_assets:
        patch["patch"]["DH_ENABLE_5M_BTC"] = "true"
    if "eth" in expect_assets:
        patch["patch"]["DH_ENABLE_5M_ETH"] = "true"
    # Single write: patch + reset session + resume (second write used to wipe patch)
    patch["control"] = "reset_lih_session"
    ro(c, f"printf '%s' '{json.dumps(patch)}' > '{PROJ}/logs/runtime_config.json'")
    time.sleep(4)
    resume = {
        "patch": patch["patch"],
        "control": "resume",
        "user": "live-test-round",
    }
    ro(c, f"printf '%s' '{json.dumps(resume)}' > '{PROJ}/logs/runtime_config.json'")
    time.sleep(3)
    snap = api_snapshot(c)
    print(json.dumps(snap, indent=None), flush=True)
    if int(snap.get("riskMax") or 0) != risk_max:
        print(f"WARN: riskMax not {risk_max} after enable", flush=True)


def asset_slot() -> dict:
    return {
        "leg1": False,
        "hedge_ok": False,
        "hedge_fail": False,
        "closed": False,
        "round_id": "",
        "hedge_ok_at": 0.0,
        "fail_since": None,
    }


def _hint_asset(ln: str) -> str | None:
    m = ASSET_HINT_RE.search(ln)
    if m:
        return m.group(1).lower()
    m = ROUND_ID_RE.search(ln)
    if m:
        return m.group(1).lower()
    return None


def _asset_from_round_id(rid: str) -> str | None:
    m = re.match(r"LIH-([a-z]+)-\d+", rid, re.I)
    return m.group(1).lower() if m else None


def parse_log_line(ln: str, assets: dict[str, dict]) -> None:
    asset = _hint_asset(ln)
    m_leg1 = LEG1_OK_RE.search(ln)
    if m_leg1:
        g = m_leg1.groups()
        if g[0] and g[1]:
            asset = g[0].lower()
        elif g[2] and g[3]:
            asset = _asset_from_round_id(g[2]) or asset
            if g[2]:
                rid = g[2]
        elif g[4]:
            asset = g[4].lower()
        elif g[5]:
            asset = g[5].lower()
        if asset:
            slot = assets.setdefault(asset, asset_slot())
            slot["leg1"] = True
            rid_m = ROUND_ID_RE.search(ln)
            if rid_m:
                slot["round_id"] = rid_m.group(0)
            elif m_leg1.group(2) and str(m_leg1.group(2)).startswith("LIH-"):
                slot["round_id"] = m_leg1.group(2)
    m_hedge = HEDGE_OK_RE.search(ln)
    if m_hedge:
        g = m_hedge.groups()
        if g[0] and g[1]:
            asset = g[0].lower()
        elif g[2] and g[3]:
            asset = _asset_from_round_id(g[2]) or asset
        elif g[4]:
            asset = g[4].lower()
        elif g[5]:
            asset = g[5].lower()
        slot = assets.setdefault(asset, asset_slot())
        slot["hedge_ok"] = True
        slot["hedge_ok_at"] = time.time()
        slot["fail_since"] = None
    if HEDGE_FAIL_RE.search(ln):
        if not asset:
            # attribute to most recent leg1 without outcome
            for a, s in assets.items():
                if s["leg1"] and not s["hedge_ok"] and not s["hedge_fail"]:
                    asset = a
                    break
        if asset:
            slot = assets.setdefault(asset, asset_slot())
            if slot["leg1"]:
                slot["hedge_fail"] = True
                slot["fail_since"] = time.time()
    if "CLOSED" in ln and asset:
        assets.setdefault(asset, asset_slot())["closed"] = True


def asset_outcome(a: str, slot: dict, now: float) -> str | None:
    if not slot.get("leg1"):
        return None
    if slot.get("hedge_ok") and now - slot.get("hedge_ok_at", 0) >= SUCCESS_GRACE_SEC:
        return f"{a}:hedge-ok"
    if slot.get("hedge_fail") and slot.get("fail_since"):
        if now - slot["fail_since"] >= RETRY_GRACE_SEC:
            return f"{a}:hedge-failed"
    if slot.get("closed"):
        return f"{a}:closed-unhedged" if not slot.get("hedge_ok") else f"{a}:hedge-ok-closed"
    return None


def all_outcomes_ready(assets: dict[str, dict], expect: list[str], now: float) -> str | None:
    active = {a: s for a, s in assets.items() if s.get("leg1")}
    if not active:
        return None

    results: dict[str, str] = {}
    for a, s in active.items():
        o = asset_outcome(a, s, now)
        if o:
            results[a] = o.split(":", 1)[1]

    if not results:
        return None

    # All assets that opened leg1 must have a resolved outcome
    if all(a in results for a in active):
        ok = sum(1 for r in results.values() if r.startswith("hedge-ok"))
        fail = len(results) - ok
        return f"dual-done ok={ok} fail={fail} detail={results}"

    # If expecting 2 assets but only 1 leg1 so far, keep waiting unless that one is done
    # and second never appeared for 90s
    return None


def print_outcomes(assets: dict[str, dict], reason: str, snap: dict) -> None:
    print(f"\n>>> 本局结果 ({reason})", flush=True)
    active = {a: s for a, s in assets.items() if s.get("leg1")}
    if not active:
        print("    无 leg1 成交", flush=True)
    for a, s in sorted(active.items()):
        if s.get("hedge_ok"):
            tag = "成 — 对冲成交"
        elif s.get("hedge_fail"):
            tag = "败 — 对冲未成交"
        elif s.get("closed"):
            tag = "败 — 结算时单腿"
        else:
            tag = "未定"
        print(f"    {a.upper()}: {tag}  round={s.get('round_id') or '?'}", flush=True)
    ops = snap.get("ops") or []
    for p in ops:
        print(
            f"    持仓 {p.get('asset')} Y={p.get('yesSize')} N={p.get('noSize')} "
            f"gap={p.get('gap')} hedged={p.get('fullyHedged')}",
            flush=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Monitor LIH test round. Default: watch-only (no new entries)."
    )
    ap.add_argument(
        "--enable-live",
        action="store_true",
        help="DANGER: allow one live test round (sets riskMax=1, then auto-stops)",
    )
    ap.add_argument(
        "--watch-only",
        action="store_true",
        help="Explicit watch-only (default behaviour)",
    )
    ap.add_argument("--max-wait", type=int, default=MAX_WAIT_SEC)
    ap.add_argument("--risk-max", type=int, default=1)
    ap.add_argument(
        "--expect-assets",
        default="btc",
        help="Comma-separated assets to enable/watch, e.g. btc,eth",
    )
    args = ap.parse_args()
    if args.enable_live and args.watch_only:
        print("ERROR: use either --enable-live or --watch-only, not both", file=sys.stderr)
        return 2
    live_enable = bool(args.enable_live)
    expect = [x.strip().lower() for x in args.expect_assets.split(",") if x.strip()]
    session_legs = 2 if len(expect) == 1 else max(2, len(expect) * 2)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=60)
    stop_reason = "timeout"
    assets: dict[str, dict] = {}
    first_leg1_at: float | None = None
    armed_stop = {"done": False}

    def _atexit_stop() -> None:
        if not live_enable or armed_stop["done"]:
            return
        try:
            stop_new_entries(c, stop_reason)
            armed_stop["done"] = True
        except Exception as exc:
            print(f"atexit stop failed: {exc}", flush=True)

    atexit.register(_atexit_stop)
    try:
        snap0 = api_snapshot(c)
        print(
            f"=== MODE: {'LIVE ONE-ROUND' if live_enable else 'WATCH-ONLY (no entries)'} ===",
            flush=True,
        )
        print(
            f"start riskMax={snap0.get('riskMax')} open={snap0.get('open')} "
            f"status={snap0.get('status')} reason={snap0.get('reason')}",
            flush=True,
        )
        if live_enable:
            if int(snap0.get("riskMax") or 0) > 0 and int(snap0.get("open") or 0) > 0:
                print("ERROR: open position already — run emergency stop first", file=sys.stderr)
                return 2
            enable_round(c, args.risk_max, session_legs, expect)
        elif int(snap0.get("riskMax") or 0) > 0:
            print(
                "WARN: riskMax>0 but watch-only — will NOT enable; bot may still trade if resumed",
                flush=True,
            )
        line_count = int(ro(c, f"wc -l < '{PROJ}/bot.log'").split()[0] or "0")
        print(
            f"\n=== MONITOR {expect} until all outcomes (max {args.max_wait}s) ===",
            flush=True,
        )
        deadline = time.time() + args.max_wait
        last_status = 0.0

        while time.time() < deadline:
            now = time.time()
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            try:
                total = int(ro(c, f"wc -l < '{PROJ}/bot.log'").split()[0])
            except ValueError:
                total = line_count
            if total > line_count:
                chunk = ro(c, f"tail -n +{line_count + 1} '{PROJ}/bot.log' | tail -n 400")
                for ln in chunk.splitlines():
                    if not WATCH.search(ln):
                        continue
                    print(f"[{ts}] {ln}", flush=True)
                    parse_log_line(ln, assets)
                    if any(s.get("leg1") for s in assets.values()) and first_leg1_at is None:
                        first_leg1_at = now
                    if "[LIH LIVE] CLOSED" in ln:
                        for a, s in assets.items():
                            if s.get("leg1"):
                                s["closed"] = True
                        if live_enable:
                            stop_reason = "round-closed"
                            print(f"\n[{ts}] round closed — stopping monitor", flush=True)
                            break
                line_count = total
                if stop_reason == "round-closed":
                    snap = api_snapshot(c)
                    print_outcomes(assets, stop_reason, snap)
                    break

            snap = api_snapshot(c)
            if now - last_status >= 24:
                last_status = now
                leg1_list = [a for a, s in assets.items() if s.get("leg1")]
                print(
                    f"[{ts}] open={snap.get('open')} sess={snap.get('sess')}/"
                    f"{snap.get('sessMax')} risk={snap.get('riskMax')} "
                    f"oneSlot={snap.get('oneSlot')} eth5m={snap.get('eth5m')} | "
                    f"leg1={leg1_list or '-'}",
                    flush=True,
                )

            done = all_outcomes_ready(assets, expect, now)
            if done:
                stop_reason = done
                print(f"\n[{ts}] {done}", flush=True)
                print_outcomes(assets, done, snap)
                break

            # Single-asset fast path when risk_max=1
            if args.risk_max == 1 and len(expect) == 1:
                a = expect[0]
                s = assets.get(a, asset_slot())
                o = asset_outcome(a, s, now)
                if o:
                    stop_reason = o
                    print(f"\n[{ts}] {o}", flush=True)
                    print_outcomes(assets, o, snap)
                    break

            # Dual: if only one leg1 ever and it's resolved, wait 90s for second asset
            leg1_assets = [a for a, s in assets.items() if s.get("leg1")]
            if (
                len(expect) > 1
                and len(leg1_assets) == 1
                and first_leg1_at
                and now - first_leg1_at > 90
            ):
                o = asset_outcome(leg1_assets[0], assets[leg1_assets[0]], now)
                if o:
                    stop_reason = f"single-asset-only {o}"
                    print(f"\n[{ts}] {stop_reason}", flush=True)
                    print_outcomes(assets, stop_reason, snap)
                    break

            time.sleep(POLL_SEC)

        if stop_reason == "timeout":
            if not any(s.get("leg1") for s in assets.values()):
                stop_reason = "timeout-no-leg1"
            snap = api_snapshot(c)
            print_outcomes(assets, stop_reason, snap)

        print("\n=== SUMMARY ===", flush=True)
        for a, s in sorted(assets.items()):
            rid = s.get("round_id") or a
            print(f"--- {a.upper()} ---")
            print(
                ro(
                    c,
                    f"grep -aE 'LIH LIVE (LEG1|HEDGE) {a}|{rid}' '{PROJ}/bot.log' "
                    f"| grep -aE 'LEG1|HEDGE|CLOSED|dead|skip|abandon' | tail -12",
                )
            )
            print()
        print(json.dumps(api_snapshot(c), indent=2), flush=True)
        return 0
    finally:
        if live_enable and not armed_stop["done"]:
            try:
                snap = api_snapshot(c)
                if int(snap.get("open") or 0) > 0:
                    print(
                        f"\nWARN: open={snap.get('open')} — skip riskMax=0 pause; "
                        "wait for CLOSED then run stop manually",
                        flush=True,
                    )
                else:
                    stop_new_entries(c, stop_reason)
                    armed_stop["done"] = True
            except Exception as exc:
                print(f"stop failed: {exc}", flush=True)
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
