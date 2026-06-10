import os
import time
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from dotenv import load_dotenv

def derive_polymarket_keys():
    load_dotenv()
    
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        print("❌ Error: POLYMARKET_PRIVATE_KEY not found in .env file.")
        return

    if private_key.startswith("0x"):
        private_key = private_key[2:]
    
    account = Account.from_key(private_key)
    address = account.address
    print(f"🔗 Wallet Address: {address}")
    
    # 1. Get a challenge/nonce if needed, but Polymarket derivation usually uses a standard message
    # Based on docs: timestamp + "This message attests that I control the given wallet"
    timestamp = str(int(time.time()))
    nonce = 0 # Standard for derivation
    
    domain = {
        "name": "ClobAuthDomain",
        "version": "1",
        "chainId": 137
    }
    
    types = {
        "ClobAuth": [
            {"name": "address", "type": "address"},
            {"name": "timestamp", "type": "string"},
            {"name": "nonce", "type": "uint256"},
            {"name": "message", "type": "string"}
        ]
    }
    
    message = {
        "address": address,
        "timestamp": timestamp,
        "nonce": nonce,
        "message": "This message attests that I control the given wallet"
    }
    
    print("✍️  Signing derivation request...")
    signed_message = account.sign_typed_data(domain, types, message)
    signature = signed_message.signature.hex()
    
    # Ensure signature has 0x prefix
    if not signature.startswith("0x"):
        signature = "0x" + signature
    
    # 2. Try to CREATE the key first (POST)
    create_url = "https://clob.polymarket.com/auth/api-key"
    headers = {
        "POLY_ADDRESS": address.lower(),
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": timestamp,
        "POLY_NONCE": str(nonce)
    }
    
    print(f"📡 Sending Create request (POST)...")
    try:
        response = requests.post(create_url, headers=headers)
        if response.status_code in [200, 201]:
            data = response.json()
            print("\n✅ SUCCESS! Created NEW keys:")
            print("-" * 40)
            print(f"POLY_API_KEY={data['apiKey']}")
            print(f"POLY_API_SECRET={data['secret']}")
            print(f"POLY_PASSPHRASE={data['passphrase']}")
            print("-" * 40)
        elif "already exists" in response.text.lower() or response.status_code == 400:
            print("ℹ️  Key already exists. Attempting to DERIVE instead...")
            derive_url = "https://clob.polymarket.com/auth/derive-api-key"
            response = requests.get(derive_url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                print("\n✅ SUCCESS! Derived EXISTING keys:")
                print("-" * 40)
                print(f"POLY_API_KEY={data['apiKey']}")
                print(f"POLY_API_SECRET={data['secret']}")
                print(f"POLY_PASSPHRASE={data['passphrase']}")
                print("-" * 40)
            else:
                print(f"❌ Derive failed: {response.text}")
        else:
            print(f"❌ Create failed (Status {response.status_code}): {response.text}")
            
    except Exception as e:
        print(f"❌ Network error: {e}")

if __name__ == "__main__":
    derive_polymarket_keys()
