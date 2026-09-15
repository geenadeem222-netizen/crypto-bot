import os
import json
import asyncio
import websockets

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

print("====================================")
print("BINANCE WEBSOCKET CONNECTION TEST")
print("====================================")

if API_KEY:
    print("BINANCE_API_KEY: FOUND")
else:
    print("BINANCE_API_KEY: NOT FOUND")

if API_SECRET:
    print("BINANCE_API_SECRET: FOUND")
else:
    print("BINANCE_API_SECRET: NOT FOUND")


async def test_websocket():
    url = "wss://fstream.binance.com/ws/btcusdt@markPrice"

    print("\nConnecting to Binance Futures WebSocket...")
    print(url)

    try:
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10
        ) as ws:

            print("WEBSOCKET CONNECTED SUCCESSFULLY!")

            message = await asyncio.wait_for(ws.recv(), timeout=15)

            data = json.loads(message)

            print("\nLIVE BINANCE DATA RECEIVED:")
            print("Symbol:", data.get("s"))
            print("Mark Price:", data.get("p"))
            print("Funding Rate:", data.get("r"))
            print("Event Time:", data.get("E"))

            print("\n====================================")
            print("BINANCE WEBSOCKET TEST: SUCCESS")
            print("====================================")

    except Exception as e:
        print("\n====================================")
        print("BINANCE WEBSOCKET TEST: FAILED")
        print("ERROR:", repr(e))
        print("====================================")


asyncio.run(test_websocket())
