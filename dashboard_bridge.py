import asyncio
import json
import subprocess
import websockets
import threading
import sys
import os

# Configuration
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8080"))
CORE_CMD = ["./build/trading-core.exe"] if os.name == "nt" else ["./build/trading-core"]

clients = set()
latest_data = "{}"

async def broadcast(message):
    if clients:
        await asyncio.gather(*[client.send(message) for client in clients])

async def handler(websocket):
    global latest_data
    clients.add(websocket)
    try:
        # Send latest data immediately on connect
        await websocket.send(latest_data)
        async for message in websocket:
            pass # We don't expect messages from dashboard
    finally:
        clients.remove(websocket)

def run_core():
    global latest_data
    # Use unbuffered output to get data immediately
    process = subprocess.Popen(
        CORE_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=os.getcwd()
    )

    # Read stderr in a separate thread to log it
    def log_stderr():
        for line in process.stderr:
            print(f"[CORE LOG] {line.strip()}", file=sys.stderr)
    
    threading.Thread(target=log_stderr, daemon=True).start()

    for line in process.stdout:
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            latest_data = line
            # Use a thread-safe way to broadcast to the event loop
            asyncio.run_coroutine_threadsafe(broadcast(line), loop)
        else:
            # Not JSON, probably a log message that escaped redirection
            print(f"[CORE INFO] {line}")

async def main():
    global loop
    loop = asyncio.get_running_loop()
    
    # Start WebSocket server
    async with websockets.serve(handler, WS_HOST, WS_PORT):
        print(f"Bridge started on ws://{WS_HOST}:{WS_PORT}")
        
        # Start core in a separate thread
        threading.Thread(target=run_core, daemon=True).start()
        
        # Keep bridge running
        await asyncio.Future() # run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down bridge...")
