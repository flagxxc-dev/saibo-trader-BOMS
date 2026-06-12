"""Read/write whitelisted .env keys and append audit events."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

ENV_PATH = Path(os.getenv("ENV_PATH", ".env"))
AUDIT_PATH = Path(os.getenv("AUDIT_PATH", "logs/audit.jsonl"))
RUNTIME_CONFIG_PATH = Path(os.getenv("RUNTIME_CONFIG_PATH", "logs/runtime_config.json"))

BOOL_KEYS = frozenset({
    "BINANCE_FEED_ENABLED",
    "DH_ENABLE_5M",
    "DH_ENABLE_15M",
    "DH_ENABLE_5M_BTC",
    "DH_ENABLE_5M_ETH",
    "DH_ENABLE_5M_SOL",
    "DH_ENABLE_15M_BTC",
    "DH_ENABLE_15M_ETH",
})

ALLOWED_KEYS = {
    "DH_SUM_TARGET",
    "DH_MIN_DISCOUNT",
    "DH_COOLDOWN_SECONDS",
    "DH_MIN_SECONDS_REMAINING",
    "DH_ENABLE_5M",
    "DH_ENABLE_15M",
    "DH_ENABLE_5M_BTC",
    "DH_ENABLE_5M_ETH",
    "DH_ENABLE_5M_SOL",
    "DH_ENABLE_15M_BTC",
    "DH_ENABLE_15M_ETH",
    "RISK_MAX_POSITION_FRACTION",
    "RISK_DAILY_LOSS_LIMIT",
    "RISK_TOTAL_DRAWDOWN_KILL",
    "RISK_MAX_CONCURRENT_POSITIONS",
    "FEE_RATE",
    "BINANCE_FEED_ENABLED",
}

PUBLIC_CONFIG_KEYS = sorted(ALLOWED_KEYS)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        result[key] = val
    return result


def public_config(path: Path | None = None) -> dict[str, str]:
    env = read_env(path)
    return {k: env[k] for k in PUBLIC_CONFIG_KEYS if k in env}


def _parse_bool(text: str) -> str:
    lowered = text.lower()
    if lowered in ("true", "1", "yes", "on"):
        return "true"
    if lowered in ("false", "0", "no", "off"):
        return "false"
    raise ValueError(f"{text!r} must be true/false")


def _validate_patch(patch: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in patch.items():
        if key not in ALLOWED_KEYS:
            raise ValueError(f"Key not allowed: {key}")
        text = str(value).strip()
        if key in BOOL_KEYS:
            text = _parse_bool(text)
        elif key == "RISK_MAX_CONCURRENT_POSITIONS":
            n = int(float(text))
            if n < 1 or n > 50:
                raise ValueError("RISK_MAX_CONCURRENT_POSITIONS must be 1-50")
            text = str(n)
        elif key.startswith("RISK_") or key in (
            "DH_SUM_TARGET",
            "DH_MIN_DISCOUNT",
            "DH_COOLDOWN_SECONDS",
            "DH_MIN_SECONDS_REMAINING",
            "FEE_RATE",
        ):
            num = float(text)
            if key == "RISK_MAX_POSITION_FRACTION" and not (0.01 <= num <= 1.0):
                raise ValueError("RISK_MAX_POSITION_FRACTION must be 0.01-1.0")
            if key == "RISK_DAILY_LOSS_LIMIT" and not (0.01 <= num <= 1.0):
                raise ValueError("RISK_DAILY_LOSS_LIMIT must be 0.01-1.0")
            if key == "RISK_TOTAL_DRAWDOWN_KILL" and not (0.05 <= num <= 1.0):
                raise ValueError("RISK_TOTAL_DRAWDOWN_KILL must be 0.05-1.0")
            if key == "DH_SUM_TARGET" and not (0.5 <= num <= 1.0):
                raise ValueError("DH_SUM_TARGET must be 0.5-1.0")
            if key == "DH_MIN_DISCOUNT" and not (0.0 <= num <= 0.5):
                raise ValueError("DH_MIN_DISCOUNT must be 0.0-0.5")
            if key == "FEE_RATE" and not (0.0 <= num <= 0.1):
                raise ValueError("FEE_RATE must be 0.0-0.1")
            if key in ("DH_COOLDOWN_SECONDS", "DH_MIN_SECONDS_REMAINING") and num < 0:
                raise ValueError(f"{key} must be >= 0")
            text = str(num)
        cleaned[key] = text
    return cleaned


def update_env(patch: dict[str, Any], path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    cleaned = _validate_patch(patch)
    if not cleaned:
        return {}

    lines: list[str] = []
    seen: set[str] = set()
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    key_pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
    for line in lines:
        m = key_pattern.match(line.strip())
        if m and m.group(1) in cleaned:
            key = m.group(1)
            out.append(f"{key}={cleaned[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, val in cleaned.items():
        if key not in seen:
            out.append(f"{key}={val}")

    _ensure_parent(path)
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return cleaned


def append_audit(event: dict[str, Any]) -> None:
    _ensure_parent(AUDIT_PATH)
    payload = {"ts": int(time.time() * 1000), **event}
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_audit(limit: int = 200) -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    items.reverse()
    return items


def write_runtime_config(payload: dict[str, Any]) -> None:
    _ensure_parent(RUNTIME_CONFIG_PATH)
    out = dict(payload)
    if "patch" in out and isinstance(out["patch"], dict):
        out["patch"] = {k: str(v) for k, v in out["patch"].items()}
    RUNTIME_CONFIG_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
