"""Live CLOB order execution via official py-clob-client-v2 (V2 EIP-712)."""

from __future__ import annotations

import json
import math
import os
import re
import time
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderType, PartialCreateOrderOptions
from py_clob_client_v2.constants import POLYGON

load_dotenv()

_SCALE = 1_000_000.0
_MIN_BUY_USDC = 1.0
_ORDER_ID_RE = re.compile(r"0x[a-fA-F0-9]{64}")


def _resolve_signature_type(funder: str, signer: str) -> int:
    raw = os.getenv("POLYMARKET_SIGNATURE_TYPE", "").strip()
    if raw:
        return int(raw)
    if funder and signer and funder.lower() != signer.lower():
        return 3
    return 0


@lru_cache(maxsize=1)
def _client() -> ClobClient:
    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    signer = os.getenv("POLYMARKET_SIGNER", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY", "").strip(),
        api_secret=os.getenv("POLY_API_SECRET", "").strip(),
        api_passphrase=os.getenv("POLY_PASSPHRASE", "").strip(),
    )
    sig_type = _resolve_signature_type(funder, signer)
    return ClobClient(
        "https://clob.polymarket.com",
        key=pk,
        chain_id=POLYGON,
        signature_type=sig_type,
        funder=funder,
        creds=creds,
    )


def round_clob_buy(price: float, size_shares: float) -> tuple[float, float]:
    """Polymarket BUY: maker USDC max 2 decimals, taker shares max 4 decimals."""
    price = round(float(price), 4)
    if price <= 0 or size_shares <= 0:
        return price, 0.0
    size = math.floor(float(size_shares) * 10000 + 1e-9) / 10000
    usdc = math.floor(price * size * 100 + 1e-9) / 100
    if usdc < _MIN_BUY_USDC:
        min_size = math.ceil((_MIN_BUY_USDC / price) * 10000) / 10000
        size = min_size
        usdc = math.floor(price * size * 100 + 1e-9) / 100
    if usdc < _MIN_BUY_USDC:
        return price, 0.0
    size = math.floor((usdc / price) * 10000 + 1e-9) / 10000
    usdc = math.floor(price * size * 100 + 1e-9) / 100
    if usdc < _MIN_BUY_USDC or size <= 0:
        return price, 0.0
    return price, size


def round_clob_sell(price: float, size_shares: float) -> tuple[float, float]:
    price = round(float(price), 4)
    size = math.floor(float(size_shares) * 10000 + 1e-9) / 10000
    return price, size if size > 0 else 0.0


def _float_field(obj: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if key not in obj or obj[key] is None:
            continue
        val = obj[key]
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _parse_fill(side: str, resp: dict[str, Any]) -> tuple[float, float]:
    """Return (price, size_shares) from CLOB post_order / get_order response."""
    making = _float_field(resp, "makingAmount", "making_amount")
    taking = _float_field(resp, "takingAmount", "taking_amount")
    if making > 0 and taking > 0:
        if side.upper() == "BUY":
            shares = taking / _SCALE
            price = (making / _SCALE) / shares if shares > 0 else 0.0
        else:
            shares = making / _SCALE
            price = (taking / _SCALE) / shares if shares > 0 else 0.0
        return price, shares

    matched = _float_field(resp, "size_matched", "sizeMatched", "matched_amount", "matchedAmount")
    price = _float_field(resp, "price", "avg_price", "average_price")
    if matched > 0:
        if matched > 1000:
            matched = matched / _SCALE
        return (price if price > 0 else 0.0), matched

    raw_size = _float_field(resp, "size", "original_size", "filled_size", "filledSize")
    if raw_size > 0:
        if raw_size > 1000:
            raw_size = raw_size / _SCALE
        return (price if price > 0 else 0.0), raw_size

    # Do NOT return price with 0 shares — forces poll / activity fallback.
    return 0.0, 0.0


def _extract_order_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("orderID", "orderId", "order_id", "id"):
            val = payload.get(key)
            if val:
                return str(val)
        err = payload.get("error")
        if isinstance(err, str):
            m = _ORDER_ID_RE.search(err)
            if m:
                return m.group(0)
    text = str(payload)
    m = _ORDER_ID_RE.search(text)
    return m.group(0) if m else ""


def _activity_fill_for_token(
    token_id: str,
    side: str,
    fallback_price: float,
    *,
    since_ts: float | None = None,
) -> tuple[float, float]:
    """Last-resort: match recent wallet TRADE rows when CLOB poll returns 0 shares."""
    try:
        from clob_trades import fetch_user_trades
    except ImportError:
        return fallback_price, 0.0
    side_u = side.upper()
    cutoff = (since_ts or (time.time() - 120.0)) - 30.0
    for row in fetch_user_trades(limit=40):
        if not isinstance(row, dict) or row.get("error"):
            continue
        if str(row.get("tokenID") or "") != str(token_id):
            continue
        if str(row.get("side") or "").upper() != side_u:
            continue
        ts = float(row.get("timestamp") or 0)
        if ts > 0 and ts < cutoff:
            continue
        size = float(row.get("size") or 0)
        price = float(row.get("price") or 0)
        if size > 0:
            return (price if price > 0 else fallback_price), size
    return fallback_price, 0.0


def _poll_order_fill(
    client: ClobClient,
    order_id: str,
    side: str,
    fallback_price: float,
    *,
    token_id: str = "",
    submit_ts: float | None = None,
) -> tuple[float, float, str]:
    status = ""
    for attempt in range(14):
        if attempt > 0:
            time.sleep(0.22)
        try:
            raw = client.get_order(order_id)
            resp = raw if isinstance(raw, dict) else {}
            status = str(resp.get("status") or "")
            price, shares = _parse_fill(side, resp)
            if shares > 0:
                return (price if price > 0 else fallback_price), shares, status
            if status in ("unmatched", "cancelled", "expired", "failed"):
                return fallback_price, 0.0, status
        except Exception:
            continue
    if token_id:
        price, shares = _activity_fill_for_token(
            token_id, side, fallback_price, since_ts=submit_ts
        )
        if shares > 0:
            return price, shares, "activity"
    return fallback_price, 0.0, status


def _normalize_result(
    *,
    side: str,
    fallback_price: float,
    resp: dict[str, Any] | None,
    order_id: str,
    client: ClobClient,
    token_id: str = "",
    submit_ts: float | None = None,
) -> dict[str, Any]:
    if not order_id and resp:
        order_id = _extract_order_id(resp)

    fill_price, fill_shares = (0.0, 0.0)
    status = ""
    if resp:
        fill_price, fill_shares = _parse_fill(side, resp)
        status = str(resp.get("status") or "")

    if fill_shares <= 0 and order_id:
        polled_price, polled_shares, polled_status = _poll_order_fill(
            client,
            order_id,
            side,
            fallback_price,
            token_id=token_id,
            submit_ts=submit_ts,
        )
        if polled_shares > 0:
            fill_price, fill_shares = polled_price, polled_shares
            status = polled_status or status

    if fill_shares <= 0 and token_id:
        act_price, act_shares = _activity_fill_for_token(
            token_id, side, fallback_price, since_ts=(submit_ts or time.time()) - 600.0
        )
        if act_shares > 0:
            fill_price, fill_shares = act_price, act_shares
            status = status or "activity"

    if fill_shares <= 0:
        return {
            "success": False,
            "error": status or "0 fill after poll",
            "price": fallback_price,
            "size_shares": 0.0,
            "order_id": order_id,
            "status": status,
        }

    return {
        "success": True,
        "price": fill_price if fill_price > 0 else fallback_price,
        "size_shares": fill_shares,
        "order_id": order_id,
        "status": status or "matched",
        "error": "",
    }


def post_fak_order(
    token_id: str,
    price: float,
    size_shares: float,
    side: str,
    *,
    neg_risk: bool = False,
) -> dict[str, Any]:
    """Submit a FAK limit order; returns normalized result dict for C++ bridge."""
    side_u = side.upper()
    if side_u not in ("BUY", "SELL"):
        return {"success": False, "error": f"invalid side: {side}"}

    if side_u == "BUY":
        price, size_shares = round_clob_buy(price, size_shares)
    else:
        price, size_shares = round_clob_sell(price, size_shares)
    if size_shares <= 0:
        return {"success": False, "error": "size below exchange minimum after rounding"}

    submit_ts = time.time()
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

        client = _client()
        funder = os.getenv("POLYMARKET_FUNDER", "").strip()
        signer = os.getenv("POLYMARKET_SIGNER", "").strip()
        sig_type = _resolve_signature_type(funder, signer)
        try:
            client.update_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type)
            )
        except Exception:
            pass
        args = OrderArgsV2(
            token_id=str(token_id),
            price=float(price),
            size=float(size_shares),
            side=side_u,
        )
        opts = PartialCreateOrderOptions(neg_risk=True) if neg_risk else None
        resp = client.create_and_post_order(args, options=opts, order_type=OrderType.FAK)
    except Exception as exc:
        order_id = _extract_order_id(getattr(exc, "error_message", None) or str(exc))
        if order_id:
            try:
                client = _client()
                return _normalize_result(
                    side=side_u,
                    fallback_price=price,
                    resp=None,
                    order_id=order_id,
                    client=client,
                    token_id=str(token_id),
                    submit_ts=submit_ts,
                )
            except Exception:
                pass
        return {"success": False, "error": str(exc), "order_id": order_id}

    if not isinstance(resp, dict):
        return {"success": False, "error": f"unexpected response type: {type(resp).__name__}"}

    order_id = _extract_order_id(resp)
    error_msg = str(resp.get("errorMsg") or resp.get("error") or "")
    status = str(resp.get("status") or "")
    success = bool(resp.get("success", True))

    if not success or error_msg:
        if order_id:
            return _normalize_result(
                side=side_u,
                fallback_price=price,
                resp=resp,
                order_id=order_id,
                client=_client(),
                token_id=str(token_id),
                submit_ts=submit_ts,
            )
        return {"success": False, "error": error_msg or "order rejected", "status": status, "order_id": order_id}

    if status == "unmatched" and order_id:
        return _normalize_result(
            side=side_u,
            fallback_price=price,
            resp=resp,
            order_id=order_id,
            client=_client(),
            token_id=str(token_id),
            submit_ts=submit_ts,
        )
    if status == "unmatched":
        return {"success": False, "error": "FAK unmatched", "status": status, "order_id": order_id}

    return _normalize_result(
        side=side_u,
        fallback_price=price,
        resp=resp,
        order_id=order_id,
        client=_client(),
        token_id=str(token_id),
        submit_ts=submit_ts,
    )
