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
        print("Telegram credentials missing!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        print(f"Telegram status: {resp.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_binance_symbols_unrestricted():
    """Binance ke public market data endpoint se pairs fetch karta hai jo Geo-block nahi hota"""
    try:
        url = "https://data-api.binance.vision/api/v3/exchangeInfo"
        res = requests.get(url, timeout=10).json()
        binance_symbols = set([
            f"{s['baseAsset']}/{s['quoteAsset']}" for s in res.get('symbols', [])
            if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING' and "UP" not in s['symbol'] and "DOWN" not in s['symbol']
        ])
        return binance_symbols
    except Exception as e:
        print(f"Error fetching Binance public symbols: {e}")
        return set()

def get_filtered_symbols(mexc_exchange):
    try:
        print("Fetching markets from MEXC and Binance Public API...")
        mexc_exchange.load_markets()
        
        mexc_symbols = set([
            symbol for symbol in mexc_exchange.symbols
            if symbol.endswith("/USDT") and "UP" not in symbol and "DOWN" not in symbol
        ])

        binance_symbols = get_binance_symbols_unrestricted()
        
        # Dono exchanges ke common symbols
        common_symbols = mexc_symbols.intersection(binance_symbols)
        print(f"Total Common USDT Pairs found: {len(common_symbols)}")

        if not common_symbols:
            return []

        # MEXC tickers for volume check
        mexc_tickers = mexc_exchange.fetch_tickers(list(common_symbols))

        filtered_symbols = []
        MIN_VOLUME = 100_000_000  # $100 Million USDT

        for symbol in common_symbols:
            ticker = mexc_tickers.get(symbol)
            if ticker:
                vol = ticker.get("quoteVolume") or (ticker.get("baseVolume", 0) * ticker.get("last", 0))
                if vol and vol >= MIN_VOLUME:
                    filtered_symbols.append(symbol)

        print(f"Matched >$100M Vol Symbols ({len(filtered_symbols)}): {filtered_symbols}")
        return filtered_symbols

    except Exception as e:
        print(f"Error filtering symbols: {e}")
        return []

def scan_market():
    mexc = ccxt.mexc({'enableRateLimit': True})

    try:
        symbols = get_filtered_symbols(mexc)
        if not symbols:
            print("No symbols passed the >$100M volume filter in this cycle.")
            return

        for symbol in symbols:
            try:
                # 1. 1-Hour Timeframe Check
                ohlcv_1h = mexc.fetch_ohlcv(symbol, timeframe="1h", limit=250)
                if len(ohlcv_1h) < 200:
                    continue
                
                df_1h = pd.DataFrame(
                    ohlcv_1h,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )

                df_1h["rsi_7"] = ta.rsi(df_1h["close"], length=7)
                df_1h["rsi_14"] = ta.rsi(df_1h["close"], length=14)
                df_1h["ema_7"] = ta.ema(df_1h["close"], length=7)
                df_1h["ema_21"] = ta.ema(df_1h["close"], length=21)
                df_1h["ema_55"] = ta.ema(df_1h["close"], length=55)
                df_1h["ema_200"] = ta.ema(df_1h["close"], length=200)

                latest_1h = df_1h.iloc[-1]

                cond_1h_rsi = latest_1h["rsi_7"] < latest_1h["rsi_14"]
                cond_1h_ema = (
                    latest_1h["ema_7"] < latest_1h["ema_21"] <
                    latest_1h["ema_55"] < latest_1h["ema_200"]
                )

                if not (cond_1h_rsi and cond_1h_ema):
                    continue

                # 2. 15-Minute Timeframe Check
                ohlcv_15m = mexc.fetch_ohlcv(symbol, timeframe="15m", limit=60)
                if len(ohlcv_15m) < 50:
                    continue
                
                df_15m = pd.DataFrame(
                    ohlcv_15m,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )

                df_15m["rsi_7"] = ta.rsi(df_15m["close"], length=7)
                df_15m["rsi_14"] = ta.rsi(df_15m["close"], length=14)
                macd_15m = ta.macd(df_15m["close"], fast=12, slow=26, signal=9)
                df_15m = pd.concat([df_15m, macd_15m], axis=1)

                prev_15m = df_15m.iloc[-2]
                curr_15m = df_15m.iloc[-1]

                crossover_15m_rsi = (prev_15m["rsi_7"] >= prev_15m["rsi_14"]) and (
                    curr_15m["rsi_7"] < curr_15m["rsi_14"]
                )
                cond_15m_macd = curr_15m["MACD_12_26_9"] < curr_15m["MACDs_12_26_9"]

                if crossover_15m_rsi and cond_15m_macd:
                    msg = (
                        f"🚨 *BEARISH SIGNAL MATCHED!*\n"
                        f"Coin: `{symbol}` (Binance & MEXC Listed)\n"
                        f"- 24h Volume: > $100M\n"
                        f"- 1H: RSI(7) < RSI(14) & Bearish EMA Ribbon (7<21<55<200)\n"
                        f"- 15M: RSI Fresh Bearish Crossover & MACD Bearish"
                    )
                    send_telegram_message(msg)
                    time.sleep(2)

            except Exception as inner_e:
                print(f"Error checking {symbol}: {inner_e}")
                continue

    except Exception as e:
        print(f"Market scan error: {e}")

def run_bot():
    time.sleep(3)
    send_telegram_message("🤖 Bot Active & Fixed! Scanning Binance+MEXC pairs with >$100M Volume.")
    while True:
        scan_market()
        time.sleep(300)

threading.Thread(target=run_bot, daemon=True).start()

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
server.serve_forever()
