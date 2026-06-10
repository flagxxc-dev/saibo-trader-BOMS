"""
derive_and_update_keys.py — One-shot script to generate L2 API keys and patch .env
Run this manually or from start.sh to guarantee valid API credentials.
"""
import os
import sys

def main():
    try:
        from dotenv import load_dotenv, set_key
    except ImportError:
        print("Missing 'python-dotenv' package. Please install it.")
        sys.exit(1)

    env_path = os.path.join(os.path.dirname(__file__), '.env')
    
    # Load current vars
    load_dotenv(env_path)

    paper_mode = os.getenv("PAPER_MODE", "true").strip().lower() in ("true", "1")
    if paper_mode:
        print("[*] Paper mode is active. Skipping L2 API key derivation.")
        sys.exit(0)

    pk     = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    signer = os.getenv("POLYMARKET_SIGNER", "").strip()
    
    if not pk or not funder:
        print("CRITICAL: .env is missing POLYMARKET_PRIVATE_KEY or POLYMARKET_FUNDER.")
        sys.exit(1)

    if not pk.startswith("0x"):
        pk = "0x" + pk

    is_proxy = funder.lower() != signer.lower() if signer else True

    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.constants import POLYGON

        host = "https://clob.polymarket.com"
        
        print(f"[*] Deriving API Keys for funder: {funder}")
        print(f"[*] Wallet mode: {'PROXY (signature_type=1)' if is_proxy else 'EOA (signature_type=0)'}")

        if is_proxy:
            client = ClobClient(host, key=pk, chain_id=POLYGON, signature_type=1, funder=funder)
        else:
            client = ClobClient(host, key=pk, chain_id=POLYGON)

        creds = client.create_or_derive_api_key()
        
        if not creds.api_key or not creds.api_secret or not creds.api_passphrase:
            print("Failed to derive valid credentials.")
            sys.exit(1)

        print("[*] API credentials derived successfully. Patching .env...")
        
        set_key(env_path, "POLY_API_KEY", creds.api_key)
        set_key(env_path, "POLY_API_SECRET", creds.api_secret)
        set_key(env_path, "POLY_PASSPHRASE", creds.api_passphrase)
        
        print("\n[SUCCESS] AUTHENTICATION SUCCESSFUL! API keys saved to .env")

    except Exception as e:
        print(f"Error during key derivation: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
