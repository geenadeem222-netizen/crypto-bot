import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import time
import requests
import ccxt
import pandas as pd
import pandas_ta as ta

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

def scan_market():
    exchange = ccxt.mexc()
    try:
        exchange.load_markets()
        symbols = [
            symbol
            for symbol in exchange.symbols
            if symbol.endswith("/USDT") and not "UP" in symbol and not "DOWN" in symbol
        ]

        print(f"Scanning {len(symbols)} coins for strategy conditions...")

        for symbol in symbols:
            try:
                # 1. Fetch 1-Hour Data
                ohlcv_1h = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=50)
                if len(ohlcv_1h) < 50:
                    continue
                df_1h = pd.DataFrame(
                    ohlcv_1h,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )

                df_1h["rsi_7"] = ta.rsi(df_1h["close"], length=7)
                df_1h["rsi_14"] = ta.rsi(df_1h["close"], length=14)
                macd_1h = ta.macd(df_1h["close"], fast=12, slow=26, signal=9)
                df_1h = pd.concat([df_1h, macd_1h], axis=1)

                latest_1h = df_1h.iloc[-1]

                cond_1h_rsi = latest_1h["rsi_7"] < latest_1h["rsi_14"]
                cond_1h_macd = latest_1h["MACD_12_26_9"] < latest_1h["MACDs_12_26_9"]

                if not (cond_1h_rsi and cond_1h_macd):
                    continue

                # 2. Fetch 15-Minute Data
                ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=50)
                if len(ohlcv_15m) < 50:
                    continue
                df_15m = pd.DataFrame(
                    ohlcv_15m,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )

                df_15m["rsi_7"] = ta.rsi(df_15m["close"], length=7)
                df_15m["rsi_14"] = ta.rsi(df_15m["close"], length=14)

                prev_15m = df_15m.iloc[-2]
                curr_15m = df_15m.iloc[-1]

                crossover_15m = (prev_15m["rsi_7"] >= prev_15m["rsi_14"]) and (
                    curr_15m["rsi_7"] < curr_15m["rsi_14"]
                )

                if crossover_15m:
                    msg = (
                        f"🚨 *BEARISH ALERT MATCHED!*\nCoin: `{symbol}`\n- 1H: RSI(7) <"
                        " RSI(14) & MACD Bearish\n- 15M: RSI(7) crossed below RSI(14)!"
                    )
                    send_telegram_message(msg)
                    time.sleep(2)

            except Exception as inner_e:
                continue

    except Exception as e:
        print(f"Market fetch error: {e}")

def run_bot():
    send_telegram_message(
        "🤖 Multi-Coin Crypto Scanner Bot is online and monitoring all USDT pairs on MEXC 24/7!"
    )
    while True:
        scan_market()
        time.sleep(300)

# Bot ko background thread mein chala diya taake main thread free rahe
threading.Thread(target=run_bot, daemon=True).start()

# Main thread par HTTP server chala rahe hain taake Render ka port foran bind ho jaye
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

port = int(os.environ.get("PORT", 10000))
server = HTTPServer(('0.0.0.0', port), SimpleHandler)
server.serve_forever()
