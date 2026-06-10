import asyncio
import json
import websockets
import random
import time

async def feed(websocket, path):
    print("Dashboard connected!")
    start_time = time.time()
    trades = 0
    balance = 10000.0
    
    while True:
        uptime = time.time() - start_time
        balance += random.uniform(-5, 10)
        trades += random.randint(0, 1)
        
        data = {
            "status": 0,
            "balance": balance,
            "dailyPnl": balance - 10000.0,
            "totalPnl": balance - 10000.0,
            "openCount": 1,
            "totalTrades": trades,
            "winRate": 65.5,
            "btcData": {
                "price": 65000 + random.uniform(-100, 100),
                "delta27": random.uniform(-10, 10),
                "delta60": random.uniform(-50, 50),
                "count": int(uptime * 10)
            },
            "ethData": {
                "price": 3500 + random.uniform(-5, 5),
                "delta27": random.uniform(-1, 1),
                "delta60": random.uniform(-5, 5),
                "count": int(uptime * 10)
            },
            "solData": {
                "price": 140 + random.uniform(-0.5, 0.5),
                "delta27": random.uniform(-0.1, 0.1),
                "delta60": random.uniform(-1, 1),
                "count": int(uptime * 10)
            },
            "openPositions": [
                {
                    "asset": "btc",
                    "side": "BUY",
                    "entryPrice": 64950.0,
                    "size": 0.1,
                    "pnl": random.uniform(-10, 20),
                    "question": "Will BTC be > $65,000 at 8:00 PM?"
                }
            ],
            "dhOpportunities": [
                {
                    "asset": "btc",
                    "yesPrice": 0.48,
                    "noPrice": 0.49,
                    "combined": 0.97,
                    "discountPct": 3.0,
                    "question": "BTC Up/Down 5m"
                }
            ]
        }
        
        try:
            await websocket.send(json.dumps(data))
            await asyncio.sleep(0.5)
        except websockets.exceptions.ConnectionClosed:
            print("Dashboard disconnected")
            break

async def main():
    async with websockets.serve(feed, "127.0.0.1", 8080):
        print("Mock server started on ws://127.0.0.1:8080")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
