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
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Credentials missing!", flush=True)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        print(f"Telegram status: {resp.status_code}", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def get_combined_volume_symbols(mexc_exchange):
    try:
        print("🔍 Step 1: Fetching combined global volumes from CoinGecko...", flush=True)
        cg_url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": "false"
        }
        
        high_vol_coins = set()
        MIN_COMBINED_VOL = 100_000_000  # Combined All-Exchange $100M Volume Filter

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
            }
            resp = requests.get(cg_url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for coin in data:
                    total_vol = coin.get("total_volume") or 0
                    if total_vol >= MIN_COMBINED_VOL:
                        high_vol_coins.add(coin.get("symbol", "").upper())
                print(f"✅ CoinGecko: Found {len(high_vol_coins)} coins with >=$100M Combined Volume.", flush=True)
            else:
                print(f"⚠️ CoinGecko Rate Limited ({resp.status_code}). Falling back to MEXC high volume check...", flush=True)
        except Exception as cg_err:
            print(f"⚠️ CoinGecko API error: {cg_err}. Falling back to MEXC...", flush=True)

        print("🔄 Step 2: Loading MEXC Futures markets...", flush=True)
        time.sleep(2)  # Delay to prevent MEXC 510 Rate Limit
        mexc_exchange.load_markets()
        matched_symbols = []

        for symbol in mexc_exchange.symbols:
            if symbol.endswith(":USDT") and "UP" not in symbol and "DOWN" not in symbol:
                base_currency = symbol.split("/")[0]  # Extract coin symbol
                
                if high_vol_coins:
                    if base_currency in high_vol_coins:
                        matched_symbols.append(symbol)
                else:
                    # Fallback check directly on MEXC Futures
                    try:
                        ticker = mexc_exchange.fetch_ticker(symbol)
                        time.sleep(0.2)  # Small rate-limit delay
                        if ticker and (ticker.get("quoteVolume") or 0) >= MIN_COMBINED_VOL:
                            matched_symbols.append(symbol)
                    except Exception:
                        continue

        print(f"🎯 Matched Symbols to scan on MEXC ({len(matched_symbols)}): {matched_symbols}", flush=True)
        return matched_symbols

    except Exception as e:
        print(f"❌ Filter error: {e}", flush=True)
        return []

def scan_market():
    mexc = ccxt.mexc({
        'enableRateLimit': True,
        'rateLimit': 1200,
        'options': {'defaultType': 'swap'}  # MEXC Futures Mode
    })
    try:
        print("\n🚀 Starting new Market Scan cycle...", flush=True)
        symbols = get_combined_volume_symbols(mexc)
        if not symbols:
            print("⚠️ No symbols matched the criteria in this cycle.", flush=True)
            return

        for symbol in symbols:
            try:
                time.sleep(1)  # Prevents MEXC "Requests are too frequent" error
                
                # 1. 1-Hour Timeframe Check
                ohlcv_1h = mexc.fetch_ohlcv(symbol, timeframe="1h", limit=250)
                if len(ohlcv_1h) < 200:
                    continue

                df_1h = pd.DataFrame(ohlcv_1h, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df_1h["rsi_7"] = ta.rsi(df_1h["close"], length=7)
                df_1h["rsi_14"] = ta.rsi(df_1h["close"], length=14)
                df_1h["ema_7"] = ta.ema(df_1h["close"], length=7)
                df_1h["ema_21"] = ta.ema(df_1h["close"], length=21)
                df_1h["ema_55"] = ta.ema(df_1h["close"], length=55)
                df_1h["ema_200"] = ta.ema(df_1h["close"], length=200)

                latest_1h = df_1h.iloc[-1]
                cond_1h_rsi = latest_1h["rsi_7"] < latest_1h["rsi_14"]
                cond_1h_ema = latest_1h["ema_7"] < latest_1h["ema_21"] < latest_1h["ema_55"] < latest_1h["ema_200"]

                if not (cond_1h_rsi and cond_1h_ema):
                    continue

                # 2. 15-Minute Timeframe Check
                ohlcv_15m = mexc.fetch_ohlcv(symbol, timeframe="15m", limit=60)
                if len(ohlcv_15m) < 50:
                    continue

                df_15m = pd.DataFrame(ohlcv_15m, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df_15m["rsi_7"] = ta.rsi(df_15m["close"], length=7)
                df_15m["rsi_14"] = ta.rsi(df_15m["close"], length=14)
                macd_15m = ta.macd(df_15m["close"], fast=12, slow=26, signal=9)
                df_15m = pd.concat([df_15m, macd_15m], axis=1)

                prev_15m = df_15m.iloc[-2]
                curr_15m = df_15m.iloc[-1]

                crossover_15m_rsi = (prev_15m["rsi_7"] >= prev_15m["rsi_14"]) and (curr_15m["rsi_7"] < curr_15m["rsi_14"])
                cond_15m_macd = curr_15m["MACD_12_26_9"] < curr_15m["MACDs_12_26_9"]

                if crossover_15m_rsi and cond_15m_macd:
                    msg = (
                        f"🚨 *BEARISH ALERT MATCHED!*\n"
                        f"Coin: `{symbol}`\n"
                        f"- Combined Global Vol: >= $100M\n"
                        f"- Market: MEXC Futures\n"
                        f"- 1H: RSI & EMA Ribbon Bearish\n"
                        f"- 15M: RSI Crossover & MACD Bearish"
                    )
                    send_telegram_message(msg)
                    time.sleep(2)
            except Exception as inner_e:
                print(f"Error checking {symbol}: {inner_e}", flush=True)
                continue
    except Exception as e:
        print(f"Market scan error: {e}", flush=True)

def run_bot():
    time.sleep(3)
    print("🤖 Bot Service Thread Started!", flush=True)
    send_telegram_message("🤖 Crypto Bot Online! Active Filter: >= $100M Combined All-Exchange Volume.")
    while True:
        scan_market()
        print("💤 Scan complete. Waiting 5 minutes for next cycle...", flush=True)
        time.sleep(300)

# Run Bot in Background Thread
threading.Thread(target=run_bot, daemon=True).start()

# HTTP Web Server for Render Keep-Alive
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Server Active!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

port = int(os.environ.get("PORT", 10000))
server = HTTPServer(('0.0.0.0', port), SimpleHandler)
print(f"🌐 Server started on port {port}", flush=True)
server.serve_forever()
