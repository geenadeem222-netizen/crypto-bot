import os
import time
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# ============================================================
# TELEGRAM SETTINGS
# ============================================================
# GitHub Actions میں یہ دونوں Secrets رکھیں:
# TELEGRAM_BOT_TOKEN
# TELEGRAM_CHAT_ID

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# اگر GitHub Secrets استعمال نہیں کرنا تو یہاں values ڈال سکتے ہیں:
# TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
# TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"


# ============================================================
# BINANCE SETTINGS
# ============================================================

BINANCE_BASE_URL = "https://api.binance.com"

REQUEST_TIMEOUT = 15

# RSI settings
RSI_FAST = 7
RSI_SLOW = 14

# Binance candle limit
KLINE_LIMIT = 100

# Maximum parallel requests
MAX_WORKERS = 12

# Scanner loop
SCAN_INTERVAL_SECONDS = 5


# ============================================================
# GLOBAL DATA
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0 RSI-Multi-Timeframe-Scanner/1.0"
})

symbols = []

# Cache:
# {
#   "BTCUSDT": {
#       "1h": {...},
#       "15m": {...},
#       "5m": {...},
#       "1m": {...}
#   }
# }
rsi_cache = {}

# Prevent duplicate alerts
last_alert_candle = {}

# Locks
cache_lock = threading.Lock()
alert_lock = threading.Lock()


# ============================================================
# LOGGING
# ============================================================

def log(message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("ERROR: Telegram token/chat ID is missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        data = response.json()

        if response.ok and data.get("ok"):
            log("Telegram message sent successfully.")
            return True

        log(f"Telegram error: {data}")
        return False

    except Exception as e:
        log(f"Telegram connection error: {e}")
        return False


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None, retries=3):
    url = BINANCE_BASE_URL + endpoint

    for attempt in range(1, retries + 1):

        try:
            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                log("Binance rate limit reached. Waiting...")
                time.sleep(2 * attempt)
                continue

            if response.status_code in (418, 403):
                log(
                    f"Binance access blocked HTTP {response.status_code}. "
                    f"Waiting before retry..."
                )
                time.sleep(5 * attempt)
                continue

            log(
                f"Binance HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        except requests.RequestException as e:
            log(
                f"Binance request error "
                f"(attempt {attempt}/{retries}): {e}"
            )

            time.sleep(attempt)

    return None


# ============================================================
# GET ALL USDT SPOT SYMBOLS
# ============================================================

def load_symbols():

    log("Loading Binance Spot symbols...")

    data = binance_get("/api/v3/exchangeInfo")

    if not data:
        log("ERROR: Could not load Binance exchange information.")
        return []

    result = []

    for item in data.get("symbols", []):

        try:
            symbol = item.get("symbol", "")
            status = item.get("status", "")
            quote_asset = item.get("quoteAsset", "")
            base_asset = item.get("baseAsset", "")
            permissions = item.get("permissions", [])

            if quote_asset != "USDT":
                continue

            if status != "TRADING":
                continue

            # Exclude leveraged token style symbols
            if base_asset.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue

            # If permissions exist, require SPOT
            if permissions and "SPOT" not in permissions:
                continue

            result.append(symbol)

        except Exception:
            continue

    result = sorted(set(result))

    log(f"TOTAL USDT SPOT SYMBOLS: {len(result)}")

    return result


# ============================================================
# GET KLINES
# ============================================================

def get_klines(symbol, interval, limit=KLINE_LIMIT):

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    return binance_get(
        "/api/v3/klines",
        params=params
    )


# ============================================================
# RSI CALCULATION
# ============================================================

def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder's RSI
    for i in range(period, len(gains)):
        avg_gain = (
            ((avg_gain * (period - 1)) + gains[i])
            / period
        )

        avg_loss = (
            ((avg_loss * (period - 1)) + losses[i])
            / period
        )

    if avg_loss == 0:

        if avg_gain == 0:
            return 50.0

        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (100.0 / (1.0 + rs))


# ============================================================
# GET RSI VALUES FROM CLOSED CANDLES
# ============================================================

def get_rsi_data(symbol, interval):

    klines = get_klines(symbol, interval)

    if not klines or len(klines) < 20:
        return None

    # Binance kline format:
    # [
    #   open_time,
    #   open,
    #   high,
    #   low,
    #   close,
    #   volume,
    #   close_time,
    #   ...
    # ]

    # IMPORTANT:
    # Remove currently forming candle.
    #
    # This makes the scanner work with CLOSED candles only,
    # so signals do not repaint during an unfinished candle.

    now_ms = int(time.time() * 1000)

    closed_klines = [
        candle
        for candle in klines
        if int(candle[6]) <= now_ms
    ]

    if len(closed_klines) < 20:
        return None

    closes = []

    for candle in closed_klines:
        try:
            closes.append(float(candle[4]))
        except Exception:
            return None

    # Current RSI
    current_rsi_7 = calculate_rsi(
        closes,
        RSI_FAST
    )

    current_rsi_14 = calculate_rsi(
        closes,
        RSI_SLOW
    )

    # Previous RSI values
    previous_closes = closes[:-1]

    previous_rsi_7 = calculate_rsi(
        previous_closes,
        RSI_FAST
    )

    previous_rsi_14 = calculate_rsi(
        previous_closes,
        RSI_SLOW
    )

    if (
        current_rsi_7 is None
        or current_rsi_14 is None
        or previous_rsi_7 is None
        or previous_rsi_14 is None
    ):
        return None

    candle_time = int(closed_klines[-1][0])

    return {
        "rsi7": current_rsi_7,
        "rsi14": current_rsi_14,
        "previous_rsi7": previous_rsi_7,
        "previous_rsi14": previous_rsi_14,
        "candle_time": candle_time
    }


# ============================================================
# UPDATE ONE SYMBOL
# ============================================================

def update_symbol(symbol):

    result = {
        "symbol": symbol,
        "1h": None,
        "15m": None,
        "5m": None,
        "1m": None
    }

    try:

        # ----------------------------------------------------
        # 1 HOUR
        # ----------------------------------------------------

        data_1h = get_rsi_data(symbol, "1h")

        if data_1h is None:
            return result

        result["1h"] = data_1h

        # Condition 1
        if not (
            data_1h["rsi7"] > data_1h["rsi14"]
        ):
            return result


        # ----------------------------------------------------
        # 15 MINUTES
        # ----------------------------------------------------

        data_15m = get_rsi_data(symbol, "15m")

        if data_15m is None:
            return result

        result["15m"] = data_15m

        # Condition 2
        if not (
            data_15m["rsi7"] > data_15m["rsi14"]
        ):
            return result


        # ----------------------------------------------------
        # 5 MINUTES
        # ----------------------------------------------------

        data_5m = get_rsi_data(symbol, "5m")

        if data_5m is None:
            return result

        result["5m"] = data_5m

        # Condition 3
        if not (
            data_5m["rsi7"] > data_5m["rsi14"]
        ):
            return result


        # ----------------------------------------------------
        # 1 MINUTE
        # ----------------------------------------------------

        data_1m = get_rsi_data(symbol, "1m")

        if data_1m is None:
            return result

        result["1m"] = data_1m

        # ----------------------------------------------------
        # MAIN CONDITION
        #
        # Previous 1M:
        # RSI7 <= RSI14
        #
        # Current closed 1M:
        # RSI7 > RSI14
        #
        # This is the actual upward CROSS.
        # ----------------------------------------------------

        cross_up = (
            data_1m["previous_rsi7"]
            <=
            data_1m["previous_rsi14"]
            and
            data_1m["rsi7"]
            >
            data_1m["rsi14"]
        )

        result["cross_up"] = cross_up

        return result

    except Exception as e:

        log(f"{symbol} update error: {e}")

        return result


# ============================================================
# PROCESS SIGNAL
# ============================================================

def process_signal(result):

    if not result:
        return

    symbol = result.get("symbol")

    if not symbol:
        return

    data_1h = result.get("1h")
    data_15m = result.get("15m")
    data_5m = result.get("5m")
    data_1m = result.get("1m")

    if not data_1h:
        return

    if not data_15m:
        return

    if not data_5m:
        return

    if not data_1m:
        return

    if not result.get("cross_up", False):
        return

    # The 1-minute candle identifies this particular signal.
    signal_candle = data_1m["candle_time"]

    # Prevent duplicate alert for same 1M cross
    with alert_lock:

        if last_alert_candle.get(symbol) == signal_candle:
            return

        last_alert_candle[symbol] = signal_candle

    # Convert timestamp
    signal_time = datetime.fromtimestamp(
        signal_candle / 1000,
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    message = (
        "🟢 RSI MULTI-TIMEFRAME ALERT\n"
        "\n"
        f"💎 Coin: {symbol}\n"
        "\n"
        "✅ 1H: RSI7 > RSI14\n"
        f"   RSI7: {data_1h['rsi7']:.2f}\n"
        f"   RSI14: {data_1h['rsi14']:.2f}\n"
        "\n"
        "✅ 15M: RSI7 > RSI14\n"
        f"   RSI7: {data_15m['rsi7']:.2f}\n"
        f"   RSI14: {data_15m['rsi14']:.2f}\n"
        "\n"
        "✅ 5M: RSI7 > RSI14\n"
        f"   RSI7: {data_5m['rsi7']:.2f}\n"
        f"   RSI14: {data_5m['rsi14']:.2f}\n"
        "\n"
        "🚀 1M: RSI7 CROSSED ABOVE RSI14\n"
        f"   Previous RSI7: {data_1m['previous_rsi7']:.2f}\n"
        f"   Previous RSI14: {data_1m['previous_rsi14']:.2f}\n"
        f"   Current RSI7: {data_1m['rsi7']:.2f}\n"
        f"   Current RSI14: {data_1m['rsi14']:.2f}\n"
        "\n"
        f"🕐 Signal Candle: {signal_time}\n"
        "\n"
        "🔥 ALL CONDITIONS CONFIRMED"
    )

    log(
        f"🚨 SIGNAL FOUND: {symbol} "
        f"| 1H={data_1h['rsi7']:.2f}>{data_1h['rsi14']:.2f} "
        f"| 15M={data_15m['rsi7']:.2f}>{data_15m['rsi14']:.2f} "
        f"| 5M={data_5m['rsi7']:.2f}>{data_5m['rsi14']:.2f}"
    )

    send_telegram(message)


# ============================================================
# SCAN ALL SYMBOLS
# ============================================================

def scan_all_symbols():

    if not symbols:
        return

    log(
        f"Scanning {len(symbols)} USDT pairs..."
    )

    start_time = time.time()

    signals_found = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(update_symbol, symbol)
            for symbol in symbols
        ]

        for future in as_completed(futures):

            try:

                result = future.result()

                if result.get("cross_up", False):
                    signals_found += 1

                process_signal(result)

            except Exception as e:

                log(f"Worker error: {e}")

    elapsed = time.time() - start_time

    log(
        f"Scan completed in {elapsed:.1f}s | "
        f"Potential signals: {signals_found}"
    )


# ============================================================
# STARTUP MESSAGE
# ============================================================

def startup_message():

    message = (
        "🟢 RSI MULTI-TIMEFRAME SCANNER STARTED\n"
        "\n"
        "📊 Binance Spot USDT Pairs\n"
        "\n"
        "Conditions:\n"
        "1️⃣ 1H RSI7 > RSI14\n"
        "2️⃣ 15M RSI7 > RSI14\n"
        "3️⃣ 5M RSI7 > RSI14\n"
        "4️⃣ 1M RSI7 crosses ABOVE RSI14\n"
        "\n"
        "🚀 Scanner is now running."
    )

    send_telegram(message)


# ============================================================
# MAIN
# ============================================================

def main():

    log("=" * 70)
    log("RSI MULTI-TIMEFRAME BINANCE SCANNER")
    log("=" * 70)

    if not TELEGRAM_BOT_TOKEN:
        log("ERROR: TELEGRAM_BOT_TOKEN is not configured.")

    if not TELEGRAM_CHAT_ID:
        log("ERROR: TELEGRAM_CHAT_ID is not configured.")

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):
        log(
            "Please configure Telegram credentials "
            "before running the scanner."
        )
        return

    # Load symbols
    global symbols

    while not symbols:

        symbols = load_symbols()

        if not symbols:

            log(
                "Could not load symbols. "
                "Retrying in 30 seconds..."
            )

            time.sleep(30)

    # Telegram startup message
    startup_message()

    log(
        f"Scanner started with {len(symbols)} symbols."
    )

    # Main loop
    while True:

        try:

            scan_all_symbols()

            log(
                f"Waiting {SCAN_INTERVAL_SECONDS} seconds..."
            )

            time.sleep(SCAN_INTERVAL_SECONDS)

        except KeyboardInterrupt:

            log("Scanner stopped manually.")
            break

        except Exception as e:

            log(f"MAIN LOOP ERROR: {e}")

            time.sleep(10)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
