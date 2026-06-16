"""
fetch_balance.py — Polymarket tradable balance for the C++ bot.
Prints a single float to stdout.

Priority:
  1. CLOB v2 collateral (after update_balance_allowance cache refresh)
  2. On-chain pUSD + USDC.e + USDC on POLYMARKET_FUNDER (V2 fallback)
"""
import os
import sys

import requests
from dotenv import load_dotenv

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

RPC_URLS = [
    "https://polygon-bor.publicnode.com",
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
]


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

    # Refresh CLOB balance cache (required after on-chain deposits / V2 migration)
    try:
        auth.update_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type)
        )
        print(f"[fetch_balance] CLOB balance cache refreshed (sig_type={sig_type})", file=sys.stderr)
    except Exception as exc:
        print(f"[fetch_balance] update_balance_allowance warn: {exc}", file=sys.stderr)

    result = auth.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type)
    )
    raw = float(result.get("balance", "0") or 0)
    if raw >= 1.0:
        return raw / 1_000_000.0
    return raw


def fetch_balance() -> float:
    try:
        load_dotenv()
    except Exception:
        pass

    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    signer = os.getenv("POLYMARKET_SIGNER", "").strip()
    fallback = float(os.getenv("LIVE_STARTING_BALANCE", "0") or 0)

    if not pk or not funder:
        return fallback

    if not pk.startswith("0x"):
        pk = "0x" + pk

    clob_bal = 0.0
    try:
        clob_bal = fetch_clob_collateral(pk, funder, signer)
        if clob_bal > 0:
            print(f"[fetch_balance] CLOB collateral: ${clob_bal:.6f}", file=sys.stderr)
    except Exception as exc:
        print(f"[fetch_balance] CLOB error: {exc}", file=sys.stderr)

    chain_bal = on_chain_collateral_total(funder)

    # Use the higher of CLOB ledger vs on-chain (pUSD often on-chain before CLOB sync)
    balance = max(clob_bal, chain_bal)
    if balance <= 0:
        balance = fallback
    return balance


if __name__ == "__main__":
    print(f"{fetch_balance():.6f}")
