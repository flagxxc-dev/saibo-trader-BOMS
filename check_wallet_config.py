"""One-shot wallet config check — does not print private keys."""
import json
import os
import re
import urllib.request

from dotenv import load_dotenv

load_dotenv()

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PLACEHOLDER_KEYS = {
    "",
    "0xYourWalletPrivateKey",
    "0x0000000000000000000000000000000000000000000000000000000000000001",
}


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("false", "0", "no", "off"):
        return False
    if raw in ("true", "1", "yes", "on"):
        return True
    return default


def main():
    issues = []
    warnings = []
    ok = []

    live_dry = _env_bool("LIVE_LIH_DRY_RUN", True)
    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    signer = os.getenv("POLYMARKET_SIGNER", "").strip()
    api_key = os.getenv("POLY_API_KEY", "").strip()

    ok.append(
        f"模式: {'shadow (LIVE_LIH_DRY_RUN=true)' if live_dry else '实盘 LIVE (将发真实订单)'}"
    )

    if pk in PLACEHOLDER_KEYS or "YourWallet" in pk or "your" in pk.lower():
        issues.append("POLYMARKET_PRIVATE_KEY is still a placeholder — live trading will fail")
    elif not re.fullmatch(r"0x[0-9a-fA-F]{64}", pk):
        issues.append("POLYMARKET_PRIVATE_KEY format invalid (need 0x + 64 hex chars)")
    else:
        ok.append("POLYMARKET_PRIVATE_KEY: format OK (value hidden)")

    if not funder:
        issues.append("POLYMARKET_FUNDER is empty")
    elif not ADDR_RE.match(funder):
        issues.append("POLYMARKET_FUNDER format invalid")
    else:
        ok.append(f"POLYMARKET_FUNDER={funder}")

    if signer:
        if not ADDR_RE.match(signer):
            issues.append("POLYMARKET_SIGNER format invalid")
        else:
            ok.append(f"POLYMARKET_SIGNER={signer}")
    else:
        warnings.append("POLYMARKET_SIGNER not set — proxy mode assumed; signer defaults to funder in C++")

    if funder and signer and funder.lower() == signer.lower():
        warnings.append("FUNDER == SIGNER → EOA wallet (signature_type=0)")
    elif funder and not signer:
        warnings.append("No SIGNER + FUNDER set → typical Polymarket proxy (signature_type=1)")

    if not api_key:
        issues.append("POLY_API_KEY missing — run: python derive_and_update_keys.py")

    # On-chain collateral on funder (pUSD + USDC)
    if funder and ADDR_RE.match(funder):
        tokens = (
            ("pUSD", "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"),
            ("USDC.e", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
        )
        rpcs = (
            "https://polygon-bor.publicnode.com",
            "https://polygon-rpc.com",
        )
        total = 0.0
        for label, contract in tokens:
            data = "0x70a08231" + funder[2:].lower().zfill(64)
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": contract, "data": data}, "latest"],
                "id": 1,
            }
            bal = None
            for rpc in rpcs:
                try:
                    req = urllib.request.Request(
                        rpc,
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        result = json.loads(resp.read().decode())
                    raw = int(result.get("result", "0x0"), 16)
                    bal = raw / 1e6
                    break
                except Exception:
                    continue
            if bal is not None and bal > 0:
                ok.append(f"On-chain {label} (funder): ${bal:.2f}")
                total += bal
        if total > 0:
            ok.append(f"On-chain collateral total (funder): ${total:.2f}")
        elif total == 0:
            warnings.append("On-chain pUSD/USDC balance is $0 on funder")

        # Polymarket profile (public)
        try:
            url = f"https://data-api.polymarket.com/profile?address={funder.lower()}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                profile = json.loads(resp.read().decode())
            if isinstance(profile, dict) and profile:
                for key in ("portfolioValue", "balance", "cashBalance", "usdcBalance", "totalValue"):
                    if key in profile and profile[key] is not None:
                        ok.append(f"Polymarket profile {key}={profile[key]}")
                        break
                else:
                    warnings.append("Polymarket profile found but no standard balance field")
            else:
                warnings.append("Polymarket profile empty — address may be unused on Polymarket")
        except Exception as exc:
            warnings.append(f"Polymarket profile lookup failed: {exc}")

    # Derive signer from private key if real
    if pk not in PLACEHOLDER_KEYS and re.fullmatch(r"0x[0-9a-fA-F]{64}", pk):
        try:
            from eth_account import Account

            derived = Account.from_key(pk).address
            ok.append(f"Private key derives EOA: {derived}")
            if signer and derived.lower() != signer.lower():
                issues.append(
                    f"POLYMARKET_SIGNER ({signer}) does not match key-derived EOA ({derived})"
                )
            elif funder and derived.lower() == funder.lower() and not signer:
                warnings.append("Key EOA == FUNDER — you may be using EOA directly, not proxy wallet")
        except ImportError:
            warnings.append("eth_account not installed — cannot verify key↔signer match")
        except Exception as exc:
            issues.append(f"Private key invalid or cannot derive address: {exc}")

    print("=== WALLET CONFIG CHECK ===")
    for line in ok:
        print("[OK]", line)
    for line in warnings:
        print("[WARN]", line)
    for line in issues:
        print("[FAIL]", line)
    print("---")
    if issues:
        print("Verdict: NOT READY FOR LIVE")
    else:
        print("Verdict: CONFIG LOOKS OK — run: python derive_and_update_keys.py && python live_preflight.py")


if __name__ == "__main__":
    main()
