
import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BINANCE_URL = "https://api.binance.com"

SCAN_INTERVAL = 60          # 1 minute
RSI_PERIOD_FAST = 7
RSI_PERIOD_SLOW = 14

# How many candles are required
CANDLE_LIMIT = 100

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ============================================================
# SESSION
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 Binance-RSI-Scanner"
})

# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Telegram credentials are missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code == 200:
            logging.info("Telegram alert sent.")
            return True

        logging.error(
            "Telegram error %s: %s",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        logging.error("Telegram connection error: %s", e)

    return False


# ============================================================
# BINANCE API
# ============================================================

def binance_get(endpoint, params=None):
    url = BINANCE_URL + endpoint

    try:
        response = session.get(
            url,
            params=params,
            timeout=20
        )

        if response.status_code != 200:
            logging.error(
                "Binance HTTP %s: %s",
                response.status_code,
                response.text[:300]
            )
            return None

        return response.json()

    except Exception as e:
        logging.error("Binance connection error: %s", e)
        return None


# ============================================================
# GET USDT SYMBOLS
# ============================================================

def get_usdt_symbols():
    data = binance_get("/api/v3/exchangeInfo")

    if not data:
        return []

    symbols = []

    for item in data.get("symbols", []):
        try:
            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("isSpotTradingAllowed", False)
            ):
                symbols.append(item["symbol"])
        except Exception:
            continue

    symbols.sort()

    logging.info("USDT symbols found: %s", len(symbols))

    return symbols


# ============================================================
# GET KLINES
# ============================================================

def get_klines(symbol, interval, limit=CANDLE_LIMIT):

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not data or not isinstance(data, list):
        return None

    if len(data) < RSI_PERIOD_SLOW + 5:
        return None

    rows = []

    for candle in data:
        rows.append({
            "open_time": candle[0],
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
            "close_time": candle[6]
        })

    df = pd.DataFrame(rows)

    return df


# ============================================================
# RSI
# ============================================================

def calculate_rsi(series, period):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi


# ============================================================
# CALCULATE RSI 7 / RSI 14
# ============================================================

def add_rsi(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    df["rsi7"] = calculate_rsi(
        df["close"],
        RSI_PERIOD_FAST
    )

    df["rsi14"] = calculate_rsi(
        df["close"],
        RSI_PERIOD_SLOW
    )

    return df


# ============================================================
# HIGHER TIMEFRAME CONDITION
# ============================================================

def timeframe_bullish(symbol, interval):

    df = get_klines(
        symbol,
        interval,
        CANDLE_LIMIT
    )

    if df is None:
        return False

    df = add_rsi(df)

    if df is None:
        return False

    # Use last CLOSED candle
    row = df.iloc[-2]

    rsi7 = row["rsi7"]
    rsi14 = row["rsi14"]

    if pd.isna(rsi7) or pd.isna(rsi14):
        return False

    return rsi7 > rsi14


# ============================================================
# 1 MINUTE FRESH CROSS
# ============================================================

def one_minute_fresh_cross(symbol):

    df = get_klines(
        symbol,
        "1m",
        CANDLE_LIMIT
    )

    if df is None:
        return False

    df = add_rsi(df)

    if df is None:
        return False

    if len(df) < 5:
        return False

    # Last CLOSED candle
    previous = df.iloc[-3]
    current = df.iloc[-2]

    previous_rsi7 = previous["rsi7"]
    previous_rsi14 = previous["rsi14"]

    current_rsi7 = current["rsi7"]
    current_rsi14 = current["rsi14"]

    if any(
        pd.isna(x)
        for x in [
            previous_rsi7,
            previous_rsi14,
            current_rsi7,
            current_rsi14
        ]
    ):
        return False

    # Fresh upward cross:
    #
    # Previous closed candle:
    # RSI7 <= RSI14
    #
    # Current closed candle:
    # RSI7 > RSI14

    crossed = (
        previous_rsi7 <= previous_rsi14
        and
        current_rsi7 > current_rsi14
    )

    return crossed


# ============================================================
# COMPLETE SEQUENCE CHECK
# ============================================================

def check_symbol(symbol):

    # --------------------------------------------------------
    # STEP 1 — 1H
    # --------------------------------------------------------

    if not timeframe_bullish(symbol, "1h"):
        return False

    # --------------------------------------------------------
    # STEP 2 — 15M
    # --------------------------------------------------------

    if not timeframe_bullish(symbol, "15m"):
        return False

    # --------------------------------------------------------
    # STEP 3 — 5M
    # --------------------------------------------------------

    if not timeframe_bullish(symbol, "5m"):
        return False

    # --------------------------------------------------------
    # STEP 4 — 1M FRESH CROSS
    # --------------------------------------------------------

    if not one_minute_fresh_cross(symbol):
        return False

    return True


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():

    symbols = get_usdt_symbols()

    if not symbols:
        logging.error("No Binance USDT symbols found.")
        return

    logging.info(
        "Scanner started. Monitoring %s USDT pairs.",
        len(symbols)
    )

    # Prevent duplicate alert on same 1M candle
    alerted_candles = set()

    while True:

        cycle_start = time.time()

        logging.info(
            "========== NEW SCAN =========="
        )

        for index, symbol in enumerate(symbols, start=1):

            try:

                # ------------------------------------------------
                # Check higher timeframes first
                # ------------------------------------------------

                if not timeframe_bullish(symbol, "1h"):
                    continue

                if not timeframe_bullish(symbol, "15m"):
                    continue

                if not timeframe_bullish(symbol, "5m"):
                    continue

                # ------------------------------------------------
                # Get 1M data
                # ------------------------------------------------

                df = get_klines(
                    symbol,
                    "1m",
                    CANDLE_LIMIT
                )

                if df is None:
                    continue

                df = add_rsi(df)

                if df is None:
                    continue

                previous = df.iloc[-3]
                current = df.iloc[-2]

                previous_rsi7 = previous["rsi7"]
                previous_rsi14 = previous["rsi14"]

                current_rsi7 = current["rsi7"]
                current_rsi14 = current["rsi14"]

                if any(
                    pd.isna(x)
                    for x in [
                        previous_rsi7,
                        previous_rsi14,
                        current_rsi7,
                        current_rsi14
                    ]
                ):
                    continue

                # ------------------------------------------------
                # Fresh upward cross
                # ------------------------------------------------

                fresh_cross = (
                    previous_rsi7 <= previous_rsi14
                    and
                    current_rsi7 > current_rsi14
                )

                if not fresh_cross:
                    continue

                # ------------------------------------------------
                # Candle ID
                # ------------------------------------------------

                candle_id = (
                    symbol,
                    int(current["open_time"])
                )

                # ------------------------------------------------
                # Duplicate protection
                # ------------------------------------------------

                if candle_id in alerted_candles:
                    continue

                alerted_candles.add(candle_id)

                # Keep memory under control
                if len(alerted_candles) > 10000:
                    alerted_candles = set(
                        list(alerted_candles)[-5000:]
                    )

                # ------------------------------------------------
                # Alert
                # ------------------------------------------------

                utc_time = datetime.now(
                    timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC")

                message = (
                    "🚨 RSI SEQUENCE ALERT 🚨\n\n"
                    f"Coin: {symbol}\n\n"
                    "1H: RSI7 > RSI14 ✅\n"
                    "15M: RSI7 > RSI14 ✅\n"
                    "5M: RSI7 > RSI14 ✅\n"
                    "1M: RSI7 crossed ABOVE RSI14 ✅\n\n"
                    f"1M RSI7: {current_rsi7:.2f}\n"
                    f"1M RSI14: {current_rsi14:.2f}\n\n"
                    f"Time: {utc_time}"
                )

                logging.info(
                    "SIGNAL FOUND: %s",
                    symbol
                )

                send_telegram(message)

            except Exception as e:

                logging.error(
                    "%s error: %s",
                    symbol,
                    e
                )

            # Small delay
            time.sleep(0.03)

        elapsed = time.time() - cycle_start

        logging.info(
            "Scan completed in %.1f seconds.",
            elapsed
        )

        # --------------------------------------------------------
        # Wait until next minute
        # --------------------------------------------------------

        sleep_time = max(
            5,
            SCAN_INTERVAL - elapsed
        )

        logging.info(
            "Next scan in %.1f seconds.",
            sleep_time
        )

        time.sleep(sleep_time)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    logging.info(
        "=========================================="
    )

    logging.info(
        "BINANCE RSI SEQUENCE SCANNER STARTING"
    )

    logging.info(
        "Sequence: 1H > 15M > 5M > 1M CROSS"
    )

    logging.info(
        "=========================================="
    )

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        logging.error(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
        )

        raise SystemExit(1)

    send_telegram(
        "🟢 Binance RSI Sequence Scanner Started\n\n"
        "Sequence:\n"
        "1H RSI7 > RSI14 ✅\n"
        "15M RSI7 > RSI14 ✅\n"
        "5M RSI7 > RSI14 ✅\n"
        "1M RSI7 fresh cross ABOVE RSI14 → ALERT 🚨"
    )

    run_scanner()
