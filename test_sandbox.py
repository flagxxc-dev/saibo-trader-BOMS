import os
import time
import asyncio
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, OrderType
from py_clob_client_v2.constants import POLYGON

async def test_sandbox():
    load_dotenv()
    
    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    signer = os.getenv("POLYMARKET_SIGNER", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    api_key = os.getenv("POLY_API_KEY", "").strip()
    api_secret = os.getenv("POLY_API_SECRET", "").strip()
    api_passphrase = os.getenv("POLY_PASSPHRASE", "").strip()
    
    if not pk.startswith("0x"):
        pk = "0x" + pk
        
    print(f"Signer: {signer}")
    print(f"Funder: {funder}")
    
    host = "https://clob.polymarket.com"
    
    try:
        from py_clob_client_v2.clob_types import ApiCreds
        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase
        )
        
        signature_type = 1 if funder.lower() != signer.lower() else 0
        client = ClobClient(
            host, 
            key=pk, 
            chain_id=POLYGON,
            signature_type=signature_type,
            funder=funder,
            creds=creds
        )
        
        # Test fetching markets
        print("\n--- Sandbox Test ---")
        print("Fetching active BTC up/down market...")
        
        # Get active markets
        events = client.get_markets()
        print(f"Markets fetched: {len(events)}")
        
        # This script can be run to test the SDK works
        print("V2 API Sandbox Test complete!")
        
    except Exception as e:
        print(f"Sandbox test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_sandbox())
