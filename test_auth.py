"""
Polymarket Authentication Test - Using the Official SDK
This bypasses all hand-rolled header logic and uses the exact same
code path that Polymarket's own trading interface uses.
"""
import os
from dotenv import load_dotenv

def test_auth():
    load_dotenv()
    
    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    signer = os.getenv("POLYMARKET_SIGNER", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    
    if not pk:
        print("❌ POLYMARKET_PRIVATE_KEY is missing from .env")
        return
    
    # Ensure private key has 0x prefix
    if not pk.startswith("0x"):
        pk = "0x" + pk
        
    print(f"🔗 Signer (MetaMask): {signer}")
    print(f"🔗 Funder (Poly Proxy): {funder}")
    
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.constants import POLYGON
    except ImportError:
        print("❌ py-clob-client-v2 not installed. Installing now...")
        import subprocess
        subprocess.check_call(["pip", "install", "py-clob-client-v2", "--break-system-packages"])
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.constants import POLYGON
    
    host = "https://clob.polymarket.com"
    
    # --- TEST 1: Public Endpoint ---
    print("\n--- TEST 1: Public Access ---")
    try:
        public_client = ClobClient(host)
        server_time = public_client.get_server_time()
        print(f"✅ Server Time: {server_time}")
    except Exception as e:
        print(f"❌ Public access failed: {e}")
        return
    
    # --- TEST 2: Authenticated Client (Proxy Wallet) ---
    print("\n--- TEST 2: Derive/Create API Key ---")
    try:
        # For proxy wallets: signature_type=1, funder=proxy_address
        if funder and funder.lower() != signer.lower():
            print("Using Proxy Wallet mode (signature_type=1)")
            client = ClobClient(
                host, 
                key=pk, 
                chain_id=POLYGON,
                signature_type=1,
                funder=funder
            )
        else:
            print("Using EOA mode (signature_type=0)")
            client = ClobClient(
                host, 
                key=pk, 
                chain_id=POLYGON
            )
        
        # Try to derive or create API credentials
        creds = client.create_or_derive_api_key()
        print(f"\n✅ API CREDENTIALS OBTAINED!")
        print("-" * 40)
        print(f"POLY_API_KEY={creds.api_key}")
        print(f"POLY_API_SECRET={creds.api_secret}")
        print(f"POLY_PASSPHRASE={creds.api_passphrase}")
        print("-" * 40)
        
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        return
    
    # --- TEST 3: Use the API key to fetch something ---
    print("\n--- TEST 3: Fetch Order Book (Authenticated) ---")
    try:
        if funder and funder.lower() != signer.lower():
            auth_client = ClobClient(
                host,
                key=pk,
                chain_id=POLYGON,
                signature_type=1,
                funder=funder,
                creds=creds
            )
        else:
            auth_client = ClobClient(
                host,
                key=pk,
                chain_id=POLYGON,
                creds=creds
            )
        
        # Try to get open orders (this is the real auth test)
        open_orders = auth_client.get_orders()
        print(f"✅ AUTHENTICATION SUCCESSFUL!")
        print(f"Open Orders: {open_orders}")
        print("\n🚀 YOU ARE READY TO GO LIVE!")
    except Exception as e:
        print(f"❌ Authenticated request failed: {e}")

if __name__ == "__main__":
    test_auth()
