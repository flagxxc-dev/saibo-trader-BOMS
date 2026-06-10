"""
fetch_balance.py — Real authenticated Polymarket balance fetch.
Called by the C++ core via popen(). Prints a single float to stdout.
"""
import os
import sys
from dotenv import load_dotenv

def fetch_balance():
    try:
        load_dotenv()
    except Exception:
        pass

    pk     = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER",      "").strip()
    signer = os.getenv("POLYMARKET_SIGNER",      "").strip()

    # Fallback value if everything fails
    fallback = os.getenv("LIVE_STARTING_BALANCE", "0.00").strip()

    if not pk or not funder:
        print(fallback)
        return

    if not pk.startswith("0x"):
        pk = "0x" + pk

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        host = "https://clob.polymarket.com"

        # Proxy wallet mode (signature_type=1)
        is_proxy = funder.lower() != signer.lower() if signer else True
        sig_type = 1 if is_proxy else 0

        if is_proxy:
            client = ClobClient(host, key=pk, chain_id=POLYGON,
                                signature_type=1, funder=funder)
        else:
            client = ClobClient(host, key=pk, chain_id=POLYGON)

        creds = client.create_or_derive_api_creds()

        if is_proxy:
            auth_client = ClobClient(host, key=pk, chain_id=POLYGON,
                                     signature_type=1, funder=funder, creds=creds)
        else:
            auth_client = ClobClient(host, key=pk, chain_id=POLYGON, creds=creds)

        # Fetch internal USDC balance from the CTF Exchange
        result = auth_client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=sig_type
            )
        )

        raw = result.get("balance", "0")
        # Polymarket returns micro-USDC (6 decimals) as a string
        balance = float(raw)
        if balance > 1_000_000:          # raw micro-USDC
            balance = balance / 1_000_000.0
        elif balance == 0:
            # Might already be in USDC units — also try portfolio API
            balance = fetch_portfolio_balance(funder, signer, fallback)

        print(f"{balance:.6f}")

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        # Last resort: try the portfolio API
        try:
            balance = fetch_portfolio_balance(funder, signer, fallback)
            print(f"{balance:.6f}")
        except Exception:
            print(fallback)


def fetch_portfolio_balance(funder: str, signer: str, fallback: str) -> float:
    """Query the Polymarket Data API for portfolio cash balance."""
    import requests
    for addr in [funder, signer]:
        if not addr:
            continue
        try:
            r = requests.get(
                f"https://data-api.polymarket.com/profile?address={addr.lower()}",
                timeout=8
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    # Field names vary — try several
                    for key in ("portfolioValue", "balance", "cashBalance",
                                "totalValue", "usdcBalance"):
                        val = data.get(key)
                        if val is not None:
                            return float(val)
        except Exception:
            pass
    return float(fallback)


if __name__ == "__main__":
    fetch_balance()
