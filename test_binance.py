import asyncio
import json
import websockets

URL = "wss://fstream.binance.com/ws/btcusdt@ticker"


async def main():
    print("====================================")
    print("BINANCE FUTURES WEBSOCKET TEST 2")
    print("====================================")
    print("Connecting...")
    print(URL)

    try:
        async with websockets.connect(
            URL,
            ping_interval=20,
            ping_timeout=None,
            close_timeout=10
        ) as ws:

            print("WEBSOCKET CONNECTED SUCCESSFULLY!")
            print("Waiting for live data...\n")

            for i in range(5):
                message = await asyncio.wait_for(
                    ws.recv(),
                    timeout=30
                )

                data = json.loads(message)

                print("LIVE DATA RECEIVED!")
                print("Symbol:", data.get("s"))
                print("Last Price:", data.get("c"))
                print("24h Volume:", data.get("q"))
                print("Event Time:", data.get("E"))
                print("------------------------------------")

            print("\n====================================")
            print("BINANCE WEBSOCKET TEST 2: SUCCESS")
            print("====================================")

    except Exception as e:
        print("\n====================================")
        print("BINANCE WEBSOCKET TEST 2: FAILED")
        print("ERROR:", repr(e))
        print("====================================")


asyncio.run(main())
