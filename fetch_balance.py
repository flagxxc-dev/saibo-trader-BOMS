"""
fetch_balance.py — Polymarket wallet balance for the C++ bot / dashboard.

Stdout: one float = **total wallet value** (cash + open positions), matching Polymarket UI.

Cash = max(CLOB collateral, on-chain pUSD+USDC) on POLYMARKET_FUNDER.
Positions = sum(currentValue) from Data API (optional; 0 if unreachable).

Run on server:
  cd /opt/polymarket-bot && .venv/bin/python fetch_balance.py
  .venv/bin/python fetch_balance.py --json   # breakdown

Requires POLYMARKET_FUNDER (POLYMARKET_PRIVATE_KEY optional for CLOB refresh).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

import requests
from dotenv import load_dotenv

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
DATA_API = "https://data-api.polymarket.com"
UA = {"User-Agent": "polymarket-bot/1.0", "Accept": "application/json"}

PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

RPC_URLS = [
    "https://polygon-bor.publicnode.com",
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
]


def _strip_env(val: str) -> str:
    v = (val or "").strip().strip("'\"")
    if v.startswith("#"):
        return ""
    return v


def resolve_funder() -> str:
    """Wallet that holds Polymarket collateral (proxy), not the EOA signer."""
    load_dotenv()
    funder = _strip_env(os.getenv("POLYMARKET_FUNDER", ""))
    if funder:
        return funder if funder.startswith("0x") else f"0x{funder}"
    # Last resort: signer (often wrong for proxy wallets — set POLYMARKET_FUNDER)
    signer = _strip_env(os.getenv("POLYMARKET_SIGNER", ""))
    if signer:
        print("[fetch_balance] warn: POLYMARKET_FUNDER unset, using SIGNER", file=sys.stderr)
        return signer if signer.startswith("0x") else f"0x{signer}"
    return ""


def on_chain_erc20_balance(holder: str, contract: str) -> float:
    holder = holder.strip().lower()
    if holder.startswith("0x"):
        holder = holder[2:]
    data = "0x70a08231" + holder.zfill(64)
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": contract, "data": data}, "latest"],
        "id": 1,
    }
    for rpc in RPC_URLS:
        try:
            resp = requests.post(rpc, json=payload, timeout=12)
            if resp.status_code != 200:
                continue
            result = resp.json().get("result", "0x0")
            raw = int(result, 16)
            return raw / 1_000_000.0
        except Exception:
            continue
    return 0.0


def on_chain_collateral_total(funder: str) -> float:
    total = 0.0
    for label, contract in (("pUSD", PUSD), ("USDC.e", USDC_E), ("USDC", USDC)):
        bal = on_chain_erc20_balance(funder, contract)
        if bal > 0:
            print(f"[fetch_balance] on-chain {label}: ${bal:.6f}", file=sys.stderr)
        total += bal
    return total


from clob_live import _resolve_signature_type as resolve_signature_type


def fetch_clob_collateral(pk: str, funder: str, signer: str) -> float:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

    sig_type = resolve_signature_type(funder, signer)
    client = ClobClient(
        HOST, key=pk, chain_id=CHAIN_ID, signature_type=sig_type, funder=funder
    )
    creds = client.derive_api_key()
    auth = ClobClient(
        HOST, key=pk, chain_id=CHAIN_ID, signature_type=sig_type, funder=funder, creds=creds
    )
    try:
        auth.update_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type)
        )
        print(f"[fetch_balance] CLOB cache refreshed (sig_type={sig_type})", file=sys.stderr)
    except Exception as exc:
        print(f"[fetch_balance] update_balance_allowance warn: {exc}", file=sys.stderr)

    result = auth.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type)
    )
    raw = float(result.get("balance", "0") or 0)
    if raw >= 1.0:
        return raw / 1_000_000.0
    return raw


def fetch_positions_value(user: str) -> tuple[float, int]:
    params = urllib.parse.urlencode(
        {"user": user.lower(), "limit": 500, "sizeThreshold": 0.01}
    )
    req = urllib.request.Request(f"{DATA_API}/positions?{params}", headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.loads(resp.read().decode())
    if not isinstance(rows, list):
        return 0.0, 0
    total = 0.0
    for row in rows:
        if isinstance(row, dict):
            total += float(row.get("currentValue") or row.get("value") or 0)
    return total, len(rows)


def fetch_balance_detail() -> dict[str, float | int | str]:
    load_dotenv()
    # Shadow observation: simulate sizing budget from real CLOB signals (no real orders).
    dry = _strip_env(os.getenv("LIVE_LIH_DRY_RUN", "")).lower() in ("1", "true", "yes", "on")
    sim = float(_strip_env(os.getenv("LIH_SHADOW_SIM_BALANCE_USDC", "0")) or 0)
    if dry and sim > 0:
        return {
            "funder": _strip_env(os.getenv("POLYMARKET_FUNDER", "")),
            "cash": sim,
            "positions": 0.0,
            "position_count": 0,
            "total": sim,
            "source": "shadow_sim",
        }

    funder = resolve_funder()
    signer = _strip_env(os.getenv("POLYMARKET_SIGNER", "")) or funder
    pk = _strip_env(os.getenv("POLYMARKET_PRIVATE_KEY", ""))
    fallback = float(os.getenv("LIVE_STARTING_BALANCE", "0") or 0)

    if not funder:
        return {
            "funder": "",
            "cash": fallback,
            "positions": 0.0,
            "position_count": 0,
            "total": fallback,
            "source": "fallback_no_funder",
        }

    chain_bal = on_chain_collateral_total(funder)
    clob_bal = 0.0
    if pk:
        if not pk.startswith("0x"):
            pk = "0x" + pk
        try:
            clob_bal = fetch_clob_collateral(pk, funder, signer)
            if clob_bal > 0:
                print(f"[fetch_balance] CLOB collateral: ${clob_bal:.6f}", file=sys.stderr)
        except Exception as exc:
            print(f"[fetch_balance] CLOB error: {exc}", file=sys.stderr)

    cash = max(clob_bal, chain_bal)
    pos_val, pos_n = 0.0, 0
    try:
        pos_val, pos_n = fetch_positions_value(funder)
        if pos_n:
            print(f"[fetch_balance] open positions: {pos_n} rows, ${pos_val:.6f}", file=sys.stderr)
    except Exception as exc:
        print(f"[fetch_balance] positions warn: {exc}", file=sys.stderr)

    total = cash + pos_val
    if total <= 0:
        total = fallback
        cash = fallback
        source = "fallback"
    else:
        source = "live"

    return {
        "funder": funder,
        "cash": cash,
        "positions": pos_val,
        "position_count": pos_n,
        "total": total,
        "source": source,
    }


def fetch_balance() -> float:
    return float(fetch_balance_detail()["total"])


if __name__ == "__main__":
    detail = fetch_balance_detail()
    if "--json" in sys.argv:
        print(json.dumps(detail, ensure_ascii=False))
    else:
        print(f"{detail['total']:.6f}")
