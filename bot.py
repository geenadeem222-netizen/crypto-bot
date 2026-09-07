import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()




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
  exchange = ccxt.binance()

  try:
    # Binance se markets load karein
    exchange.load_markets()

    # Sirf USDT pairs uthayein jo active hon (jaise BTC/USDT, ETH/USDT, HEMI/USDT waghera)
    symbols = [
        symbol
        for symbol in exchange.symbols
        if symbol.endswith("/USDT") and not "UP" in symbol and not "DOWN" in symbol
    ]

    print(f"Scanning {len(symbols)} coins for strategy conditions...")

    # Har coin ko check karein (aap chahay toh testing ke liye kuch coins tak محدود bhi kar sakte hain)
    for symbol in symbols:
      try:
        # 1. Fetch 1-Hour Data (Trend Confirmation)
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

        # 1h Conditions: RSI(7) < RSI(14) AND MACD Bearish
        cond_1h_rsi = latest_1h["rsi_7"] < latest_1h["rsi_14"]
        cond_1h_macd = latest_1h["MACD_12_26_9"] < latest_1h["MACDs_12_26_9"]

        if not (cond_1h_rsi and cond_1h_macd):
          continue  # Agar 1h pass nahi hua toh agle coin par chalay jao

        # 2. Fetch 15-Minute Data (Trigger Execution)
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

        # 15m Bearish Crossover
        crossover_15m = (prev_15m["rsi_7"] >= prev_15m["rsi_14"]) and (
            curr_15m["rsi_7"] < curr_15m["rsi_14"]
        )

        if crossover_15m:
          # Jaise hi kisi bhi coin par condition match ho, alert bhej do
          msg = (
              f"🚨 *BEARISH ALERT MATCHED!*\nCoin: `{symbol}`\n- 1H: RSI(7) <"
              " RSI(14) & MACD Bearish\n- 15M: RSI(7) crossed below RSI(14)!"
          )
          send_telegram_message(msg)
          time.sleep(2)  # Telegram spam rokne ke liye chota gap

      except Exception as inner_e:
        # Kisi aik coin mein error aaye toh baqi chaltay rahein
        continue

  except Exception as e:
    print(f"Market fetch error: {e}")


if __name__ == "__main__":
  send_telegram_message(
      "🤖 Multi-Coin Crypto Scanner Bot is online and monitoring all USDT"
      " pairs 24/7!"
  )

  while True:
    scan_market()
    # Poora market scan karne ke baad 5 minute ka waqfa
    time.sleep(300)
