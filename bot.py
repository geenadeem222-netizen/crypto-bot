import os
import time
import logging
import requests
import pandas as pd

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Binance Futures API
BINANCE_BASE_URLS = [
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
    "https://fapi.binance.com",
]

RSI_FAST = 7
RSI_SLOW = 14

CANDLE_LIMIT = 100

SCAN_INTERVAL = 60

REQUEST_TIMEOUT = 20


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0 Binance RSI Scanner"
})


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        logging.error("Telegram credentials missing.")

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
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:

            logging.info("Telegram alert sent.")

            return True

        logging.error(
            "Telegram HTTP %s: %s",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:

        logging.error(
            "Telegram error: %s",
            e
        )

    return False


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(endpoint, params=None):

    for base_url in BINANCE_BASE_URLS:

        url = base_url + endpoint

        try:

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            logging.info(
                "Binance response %s | %s",
                response.status_code,
                base_url
            )

            if response.status_code != 200:

                logging.warning(
                    "Binance HTTP %s from %s: %s",
                    response.status_code,
                    base_url,
                    response.text[:300]
                )

                continue

            try:

                data = response.json()

            except ValueError:

                logging.warning(
                    "Invalid JSON response from %s",
                    base_url
                )

                continue

            if data is None:

                continue

            return data

        except requests.RequestException as e:

            logging.warning(
                "Binance connection error from %s: %s",
                base_url,
                e
            )

            continue

        except Exception as e:

            logging.warning(
                "Binance unexpected error from %s: %s",
                base_url,
                e
            )

            continue

    logging.error(
        "All Binance Futures endpoints failed."
    )

    return None


# ============================================================
# GET FUTURES USDT SYMBOLS
# ============================================================

def get_usdt_symbols():

    data = binance_get(
        "/fapi/v1/exchangeInfo"
    )

    if not data:

        return []

    if not isinstance(data, dict):

        logging.error(
            "Invalid Binance exchangeInfo response."
        )

        return []

    symbols_data = data.get("symbols", [])

    if not symbols_data:

        logging.error(
            "Binance returned no symbols."
        )

        return []

    symbols = []

    for item in symbols_data:

        try:

            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("contractType") == "PERPETUAL"
            ):

                symbol = item.get("symbol")

                if symbol:

                    symbols.append(symbol)

        except Exception:

            continue

    symbols = sorted(set(symbols))

    logging.info(
        "Futures USDT symbols found: %s",
        len(symbols)
    )

    return symbols


# ============================================================
# GET KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=CANDLE_LIMIT
):

    data = binance_get(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not data:

        return None

    if not isinstance(data, list):

        return None

    if len(data) < RSI_SLOW + 5:

        return None

    rows = []

    for candle in data:

        try:

            rows.append({

                "open_time": int(candle[0]),

                "open": float(candle[1]),

                "high": float(candle[2]),

                "low": float(candle[3]),

                "close": float(candle[4]),

                "volume": float(candle[5]),

                "close_time": int(candle[6])

            })

        except Exception:

            continue

    if not rows:

        return None

    return pd.DataFrame(rows)


# ============================================================
# RSI CALCULATION
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

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# ADD RSI
# ============================================================

def add_rsi(df):

    if df is None or df.empty:

        return None

    df = df.copy()

    df["rsi7"] = calculate_rsi(
        df["close"],
        RSI_FAST
    )

    df["rsi14"] = calculate_rsi(
        df["close"],
        RSI_SLOW
    )

    return df


# ============================================================
# CHECK HIGHER TIMEFRAME
# ============================================================

def timeframe_bullish(
    symbol,
    interval
):

    df = get_klines(
        symbol,
        interval
    )

    if df is None:

        return False

    df = add_rsi(df)

    if df is None:

        return False

    if len(df) < 3:

        return False

    # Last CLOSED candle
    candle = df.iloc[-2]

    rsi7 = candle["rsi7"]

    rsi14 = candle["rsi14"]

    if pd.isna(rsi7) or pd.isna(rsi14):

        return False

    return rsi7 > rsi14


# ============================================================
# 1 MINUTE FRESH CROSS
# ============================================================

def one_minute_fresh_cross(symbol):

    df = get_klines(
        symbol,
        "1m"
    )

    if df is None:

        return False

    df = add_rsi(df)

    if df is None:

        return False

    if len(df) < 5:

        return False

    # Previous CLOSED candle
    previous = df.iloc[-3]

    # Current CLOSED candle
    current = df.iloc[-2]

    previous_rsi7 = previous["rsi7"]
    previous_rsi14 = previous["rsi14"]

    current_rsi7 = current["rsi7"]
    current_rsi14 = current["rsi14"]

    values = [
        previous_rsi7,
        previous_rsi14,
        current_rsi7,
        current_rsi14
    ]

    if any(pd.isna(x) for x in values):

        return False

    return (
        previous_rsi7 <= previous_rsi14
        and
        current_rsi7 > current_rsi14
    )


# ============================================================
# CHECK COMPLETE SEQUENCE
# ============================================================

def check_sequence(symbol):

    # 1H
    if not timeframe_bullish(
        symbol,
        "1h"
    ):

        return None

    # 15M
    if not timeframe_bullish(
        symbol,
        "15m"
    ):

        return None

    # 5M
    if not timeframe_bullish(
        symbol,
        "5m"
    ):

        return None

    # 1M
    df = get_klines(
        symbol,
        "1m"
    )

    if df is None:

        return None

    df = add_rsi(df)

    if df is None:

        return None

    if len(df) < 5:

        return None

    previous = df.iloc[-3]

    current = df.iloc[-2]

    previous_rsi7 = previous["rsi7"]
    previous_rsi14 = previous["rsi14"]

    current_rsi7 = current["rsi7"]
    current_rsi14 = current["rsi14"]

    values = [
        previous_rsi7,
        previous_rsi14,
        current_rsi7,
        current_rsi14
    ]

    if any(pd.isna(x) for x in values):

        return None

    fresh_cross = (
        previous_rsi7 <= previous_rsi14
        and
        current_rsi7 > current_rsi14
    )

    if not fresh_cross:

        return None

    return {
        "candle_id": int(current["open_time"]),
        "rsi7": float(current_rsi7),
        "rsi14": float(current_rsi14)
    }


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():

    symbols = get_usdt_symbols()

    if not symbols:

        logging.error(
            "No Binance Futures USDT symbols found."
        )

        return

    logging.info(
        "Scanner monitoring %s coins.",
        len(symbols)
    )

    alerted_candles = set()

    while True:

        scan_start = time.time()

        logging.info(
            "=========================================="
        )

        logging.info(
            "NEW RSI SEQUENCE SCAN"
        )

        logging.info(
            "=========================================="
        )

        for index, symbol in enumerate(
            symbols,
            start=1
        ):

            try:

                signal = check_sequence(
                    symbol
                )

                if signal is None:

                    continue

                candle_id = (
                    symbol,
                    signal["candle_id"]
                )

                if candle_id in alerted_candles:

                    continue

                alerted_candles.add(
                    candle_id
                )

                message = (
                    "🚨 RSI SEQUENCE ALERT 🚨\n\n"
                    f"COIN: {symbol}\n\n"
                    "1H  RSI7 > RSI14 ✅\n"
                    "15M RSI7 > RSI14 ✅\n"
                    "5M  RSI7 > RSI14 ✅\n"
                    "1M  RSI7 FRESH CROSS ABOVE RSI14 ✅\n\n"
                    f"1M RSI7: {signal['rsi7']:.2f}\n"
                    f"1M RSI14: {signal['rsi14']:.2f}"
                )

                logging.info(
                    "SIGNAL FOUND: %s",
                    symbol
                )

                send_telegram(
                    message
                )

            except Exception as e:

                logging.error(
                    "%s ERROR: %s",
                    symbol,
                    e
                )

        elapsed = (
            time.time()
            - scan_start
        )

        logging.info(
            "SCAN COMPLETE | TIME: %.1f seconds",
            elapsed
        )

        sleep_time = max(
            5,
            SCAN_INTERVAL - elapsed
        )

        logging.info(
            "WAITING %.1f SECONDS...",
            sleep_time
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    logging.info(
        "=========================================="
    )

    logging.info(
        "BINANCE FUTURES RSI SEQUENCE SCANNER"
    )

    logging.info(
        "=========================================="
    )

    logging.info(
        "SEQUENCE:"
    )

    logging.info(
        "1H RSI7 > RSI14"
    )

    logging.info(
        "15M RSI7 > RSI14"
    )

    logging.info(
        "5M RSI7 > RSI14"
    )

    logging.info(
        "1M RSI7 FRESH CROSS ABOVE RSI14"
    )

    logging.info(
        "=========================================="
    )

    if not TELEGRAM_BOT_TOKEN:

        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not TELEGRAM_CHAT_ID:

        raise SystemExit(
            "TELEGRAM_CHAT_ID is missing."
        )

    send_telegram(
        "🟢 Binance Futures RSI Scanner Started\n\n"
        "Sequence:\n"
        "1H RSI7 > RSI14\n"
        "15M RSI7 > RSI14\n"
        "5M RSI7 > RSI14\n"
        "1M RSI7 fresh cross ABOVE RSI14\n\n"
        "Scanner is now running."
    )

    run_scanner()
