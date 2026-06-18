"""
redeem_positions.py — On-chain CTF redeem for resolved Polymarket markets.

Stdout: one JSON line {"success": bool, "tx_hash": str|null, "message": str}

Uses Polymarket V2 collateral adapters (pUSD). Skips cleanly when Data API
shows no redeemable tokens (common after CLOB auto-settlement on proxy wallets).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

from dotenv import load_dotenv

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"
REDEEM_SELECTOR = "0x01b7037c"
DATA_API = "https://data-api.polymarket.com"
RPC_URLS = [
    "https://polygon-bor.publicnode.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
]


def _strip_env(val: str) -> str:
    v = (val or "").strip().strip("'\"")
    return "" if v.startswith("#") else v


def _resolve_funder() -> str:
    funder = _strip_env(os.getenv("POLYMARKET_FUNDER", ""))
    if funder and not funder.startswith("0x"):
        funder = "0x" + funder
    return funder


def _resolve_signer_eoa(pk: str) -> str:
    if not pk:
        return ""
    if not pk.startswith("0x"):
        pk = "0x" + pk
    from eth_account import Account

    return Account.from_key(pk).address


def _fetch_redeemable_for_condition(funder: str, condition_id: str) -> list[dict]:
    cid = condition_id.lower()
    params = urllib.parse.urlencode(
        {"user": funder.lower(), "sizeThreshold": 0.01, "limit": 500}
    )
    req = urllib.request.Request(
        f"{DATA_API}/positions?{params}",
        headers={"User-Agent": "polymarket-bot/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.loads(resp.read().decode())
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("conditionId", "")).lower() != cid:
            continue
        if not row.get("redeemable"):
            continue
        size = float(row.get("size") or 0)
        if size <= 0:
            continue
        out.append(row)
    return out


def _encode_redeem_calldata(condition_id: str) -> str:
    cid = condition_id[2:] if condition_id.startswith("0x") else condition_id
    cid_bytes32 = cid.zfill(64).lower()
    parent_collection_id = "0" * 64
    collateral_bytes32 = "0" * 24 + PUSD[2:].lower()
    array_offset = 32 * 4
    encoded = (
        collateral_bytes32
        + parent_collection_id
        + cid_bytes32
        + hex(array_offset)[2:].zfill(64)
        + hex(2)[2:].zfill(64)
        + hex(1)[2:].zfill(64)
        + hex(2)[2:].zfill(64)
    )
    return REDEEM_SELECTOR + encoded


def _ensure_ctf_approval(w3, account, ctf: str, adapter: str) -> None:
    # setApprovalForAll(adapter, true) on CTF — idempotent if already approved.
    ctf_cs = w3.to_checksum_address(ctf)
    adapter_cs = w3.to_checksum_address(adapter)
    sel = w3.keccak(text="setApprovalForAll(address,bool)")[:4].hex()
    data = sel + adapter_cs[2:].lower().zfill(64) + ("0" * 63 + "1")
    nonce = w3.eth.get_transaction_count(account.address)
    gas_price = w3.eth.gas_price
    tx = {
        "to": ctf_cs,
        "data": data,
        "value": 0,
        "gas": 80_000,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": 137,
    }
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if not receipt.get("status"):
        raise RuntimeError(f"CTF approval tx reverted: {tx_hash.hex()}")


def redeem_positions(condition_id: str, *, neg_risk: bool = True) -> dict:
    load_dotenv()

    cid = (condition_id or "").strip()
    if not cid:
        return {"success": False, "tx_hash": None, "message": "condition_id required"}

    paper = os.getenv("PAPER_MODE", "false").strip().lower()
    if paper in ("true", "1", "yes"):
        return {"success": True, "tx_hash": None, "message": "Paper mode — skipped"}

    pk = _strip_env(os.getenv("POLYMARKET_PRIVATE_KEY", ""))
    if not pk:
        return {"success": False, "tx_hash": None, "message": "POLYMARKET_PRIVATE_KEY missing"}

    funder = _resolve_funder()
    if not funder:
        return {"success": False, "tx_hash": None, "message": "POLYMARKET_FUNDER missing"}

    try:
        from web3 import Web3

        try:
            from web3.middleware import ExtraDataToPOAMiddleware as poa_middleware
        except ImportError:
            from web3.middleware import geth_poa_middleware as poa_middleware  # web3<7
    except ImportError:
        return {
            "success": False,
            "tx_hash": None,
            "message": "web3 not installed — add web3>=6.0.0,<8.0.0 to requirements.txt",
        }

    eoa = _resolve_signer_eoa(pk)
    redeemable = _fetch_redeemable_for_condition(funder, cid)
    if not redeemable:
        return {
            "success": True,
            "tx_hash": None,
            "message": "No redeemable tokens for condition (already settled)",
        }

    if eoa and funder.lower() != eoa.lower():
        total_val = sum(float(r.get("currentValue") or 0) for r in redeemable)
        return {
            "success": False,
            "tx_hash": None,
            "message": (
                f"Proxy wallet {funder[:10]}… holds ${total_val:.2f} redeemable — "
                "on-chain redeem from EOA not supported; redeem via Polymarket UI or relayer"
            ),
        }

    adapter = NEG_RISK_ADAPTER if neg_risk else CTF_ADAPTER
    data = _encode_redeem_calldata(cid)
    # CTF used by V2 adapter path
    ctf = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

    last_exc: Exception | None = None
    for rpc_url in RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
            w3.middleware_onion.inject(poa_middleware, layer=0)
            account = w3.eth.account.from_key(pk if pk.startswith("0x") else "0x" + pk)

            _ensure_ctf_approval(w3, account, ctf, adapter)

            nonce = w3.eth.get_transaction_count(account.address)
            gas_price = w3.eth.gas_price
            tx = {
                "to": w3.to_checksum_address(adapter),
                "data": data,
                "value": 0,
                "gas": 350_000,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": 137,
            }
            signed = account.sign_transaction(tx)
            raw_tx = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            tx_hash = w3.eth.send_raw_transaction(raw_tx)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if not receipt.get("status"):
                raise RuntimeError(f"Tx reverted: {tx_hash.hex()}")

            return {
                "success": True,
                "tx_hash": tx_hash.hex(),
                "message": f"Redeemed {cid[:18]} via {adapter[:10]}… ({rpc_url})",
            }
        except Exception as exc:
            last_exc = exc
            continue

    return {
        "success": False,
        "tx_hash": None,
        "message": str(last_exc) if last_exc else "All RPC endpoints failed",
    }


if __name__ == "__main__":
    condition = sys.argv[1] if len(sys.argv) > 1 else ""
    neg = True
    if len(sys.argv) > 2:
        neg = sys.argv[2].lower() not in ("false", "0", "no", "off")
    out = redeem_positions(condition, neg_risk=neg)
    print(json.dumps(out))
    sys.exit(0 if out.get("success") else 1)
