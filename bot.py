import os
import time
import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone

import requests
import websocket


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# BINANCE
# ============================================================

BINANCE_REST_URL = "https://fapi.binance.com"
BINANCE_WS_URL = "wss://fstream.binance.com/stream"

RSI_FAST = 7
RSI_SLOW = 14

# We use 5-minute candles as the base data.
# 500 candles gives enough history for 1H and 15M RSI.
HISTORY_5M = 500

# Do not hammer Binance during startup.
STARTUP_REQUEST_DELAY = 0.15

# Maximum streams per websocket connection.
# Binance allows up to 1024, so we stay below that.
STREAMS_PER_CONNECTION = 900

REQUEST_TIMEOUT = 30


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("RSI_SCANNER")


# ============================================================
# DATA STORAGE
# ============================================================

# symbol -> deque of:
# (5m_open_time_ms, 5m_close_price)
five_minute_data = {}

data_lock = threading.Lock()

# symbol -> last alerted 5m candle open time
last_alerted_candle = {}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials are missing.")
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
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            logger.info("Telegram alert sent.")
            return True

        logger.error(
            "Telegram error %s: %s",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        logger.error("Telegram exception: %s", e)

    return False


# ============================================================
# RSI
# ============================================================

def calculate_rsi(values, period=14):
    """
    RSI using Wilder-style exponential smoothing.
    Returns the latest RSI value.
    """

    if len(values) < period + 1:
        return None

    changes = []

    for i in range(1, len(values)):
        changes.append(values[i] - values[i - 1])

    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            (avg_gain * (period - 1)) + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1)) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (100.0 / (1.0 + rs))


# ============================================================
# BINANCE SYMBOLS
# ============================================================

def get_usdt_symbols():
    url = f"{BINANCE_REST_URL}/fapi/v1/exchangeInfo"

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 418:
            logger.error(
                "Binance IP is currently banned/rate limited: %s",
                response.text[:500]
            )
            return []

        response.raise_for_status()

        data = response.json()

        symbols = []

        for item in data.get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("contractType") == "PERPETUAL"
            ):
                symbols.append(item["symbol"])

        symbols.sort()

        logger.info(
            "USDT PERPETUALS FOUND: %d",
            len(symbols)
        )

        return symbols

    except Exception as e:
        logger.error(
            "Could not get Binance symbols: %s",
            e
        )
        return []


# ============================================================
# INITIAL 5M HISTORY
# ============================================================

def get_initial_5m_history(symbol):
    url = f"{BINANCE_REST_URL}/fapi/v1/klines"

    params = {
        "symbol": symbol,
        "interval": "5m",
        "limit": HISTORY_5M
    }

    while True:
        try:
            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            # Too many requests
            if response.status_code == 429:
                retry_after = response.headers.get(
                    "Retry-After",
                    "60"
                )

                try:
                    wait_seconds = float(retry_after)
                except Exception:
                    wait_seconds = 60

                logger.warning(
                    "429 for %s. Waiting %.1f seconds.",
                    symbol,
                    wait_seconds
                )

                time.sleep(min(max(wait_seconds, 5), 300))
                continue

            # Temporary IP ban
            if response.status_code == 418:
                retry_after = response.headers.get(
                    "Retry-After",
                    "300"
                )

                try:
                    wait_seconds = float(retry_after)
                except Exception:
                    wait_seconds = 300

                logger.error(
                    "418 IP BAN for %s. Waiting %.1f seconds.",
                    symbol,
                    wait_seconds
                )

                time.sleep(min(max(wait_seconds, 30), 1800))
                continue

            response.raise_for_status()

            rows = response.json()

            now_ms = int(
                time.time() * 1000
            )

            candles = []

            for row in rows:
                open_time = int(row[0])
                close_price = float(row[4])
                close_time = int(row[6])

                # Only CLOSED candles.
                if close_time < now_ms:
                    candles.append(
                        (open_time, close_price)
                    )

            return candles

        except Exception as e:
            logger.error(
                "History error %s: %s",
                symbol,
                e
            )

            time.sleep(10)


# ============================================================
# BUILD 15M CLOSES
# ============================================================

def build_15m_closes(candles):
    """
    Convert closed 5M candles into completed 15M closes.
    """

    groups = {}

    for open_time, close_price in candles:
        group_start = (
            open_time // (15 * 60 * 1000)
        ) * (15 * 60 * 1000)

        if group_start not in groups:
            groups[group_start] = []

        groups[group_start].append(
            (open_time, close_price)
        )

    result = []

    for group_start in sorted(groups.keys()):
        group = groups[group_start]

        group.sort(
            key=lambda x: x[0]
        )

        # Need exactly 3 five-minute candles
        # for a completed 15-minute candle.
        if len(group) >= 3:
            result.append(
                group[-1][1]
            )

    return result


# ============================================================
# BUILD 1H CLOSES
# ============================================================

def build_1h_closes(candles):
    """
    Convert closed 5M candles into completed 1H closes.
    """

    groups = {}

    for open_time, close_price in candles:
        group_start = (
            open_time // (60 * 60 * 1000)
        ) * (60 * 60 * 1000)

        if group_start not in groups:
            groups[group_start] = []

        groups[group_start].append(
            (open_time, close_price)
        )

    result = []

    for group_start in sorted(groups.keys()):
        group = groups[group_start]

        group.sort(
            key=lambda x: x[0]
        )

        # Need 12 five-minute candles
        # for a completed 1-hour candle.
        if len(group) >= 12:
            result.append(
                group[-1][1]
            )

    return result


# ============================================================
# GET CURRENT RSI VALUES
# ============================================================

def get_rsi_values(symbol):
    with data_lock:
        candles = list(
            five_minute_data.get(
                symbol,
                []
            )
        )

    if len(candles) < RSI_SLOW + 1:
        return None

    five_closes = [
        item[1]
        for item in candles
    ]

    closes_15m = build_15m_closes(
        candles
    )

    closes_1h = build_1h_closes(
        candles
    )

    if len(closes_15m) < RSI_SLOW + 1:
        return None

    if len(closes_1h) < RSI_SLOW + 1:
        return None

    rsi_5m_fast = calculate_rsi(
        five_closes,
        RSI_FAST
    )

    rsi_5m_slow = calculate_rsi(
        five_closes,
        RSI_SLOW
    )

    rsi_15m_fast = calculate_rsi(
        closes_15m,
        RSI_FAST
    )

    rsi_15m_slow = calculate_rsi(
        closes_15m,
        RSI_SLOW
    )

    rsi_1h_fast = calculate_rsi(
        closes_1h,
        RSI_FAST
    )

    rsi_1h_slow = calculate_rsi(
        closes_1h,
        RSI_SLOW
    )

    if any(
        value is None
        for value in [
            rsi_5m_fast,
            rsi_5m_slow,
            rsi_15m_fast,
            rsi_15m_slow,
            rsi_1h_fast,
            rsi_1h_slow
        ]
    ):
        return None

    return {
        "rsi_5m_fast": rsi_5m_fast,
        "rsi_5m_slow": rsi_5m_slow,
        "rsi_15m_fast": rsi_15m_fast,
        "rsi_15m_slow": rsi_15m_slow,
        "rsi_1h_fast": rsi_1h_fast,
        "rsi_1h_slow": rsi_1h_slow
    }


# ============================================================
# CHECK 5M FRESH CROSS
# ============================================================

def get_previous_5m_rsi_values(symbol):
    with data_lock:
        candles = list(
            five_minute_data.get(
                symbol,
                []
            )
        )

    if len(candles) < RSI_SLOW + 2:
        return None

    previous_candles = candles[:-1]

    previous_closes = [
        item[1]
        for item in previous_candles
    ]

    current_closes = [
        item[1]
        for item in candles
    ]

    previous_rsi_7 = calculate_rsi(
        previous_closes,
        RSI_FAST
    )

    previous_rsi_14 = calculate_rsi(
        previous_closes,
        RSI_SLOW
    )

    current_rsi_7 = calculate_rsi(
        current_closes,
        RSI_FAST
    )

    current_rsi_14 = calculate_rsi(
        current_closes,
        RSI_SLOW
    )

    if any(
        value is None
        for value in [
            previous_rsi_7,
            previous_rsi_14,
            current_rsi_7,
            current_rsi_14
        ]
    ):
        return None

    return {
        "previous_rsi_7": previous_rsi_7,
        "previous_rsi_14": previous_rsi_14,
        "current_rsi_7": current_rsi_7,
        "current_rsi_14": current_rsi_14
    }


# ============================================================
# CHECK COMPLETE RSI SEQUENCE
# ============================================================

def check_signal(symbol):
    values = get_rsi_values(symbol)

    if values is None:
        return None

    # --------------------------------------------------------
    # 1H CONDITION
    # RSI(7) < RSI(14)
    # --------------------------------------------------------

    if not (
        values["rsi_1h_fast"]
        <
        values["rsi_1h_slow"]
    ):
        return None

    # --------------------------------------------------------
    # 15M CONDITION
    # RSI(7) < RSI(14)
    # --------------------------------------------------------

    if not (
        values["rsi_15m_fast"]
        <
        values["rsi_15m_slow"]
    ):
        return None

    # --------------------------------------------------------
    # 5M FRESH CROSS
    #
    # Previous:
    # RSI7 >= RSI14
    #
    # Current:
    # RSI7 < RSI14
    # --------------------------------------------------------

    cross = get_previous_5m_rsi_values(
        symbol
    )

    if cross is None:
        return None

    if not (
        cross["previous_rsi_7"]
        >=
        cross["previous_rsi_14"]
    ):
        return None

    if not (
        cross["current_rsi_7"]
        <
        cross["current_rsi_14"]
    ):
        return None

    with data_lock:
        candles = list(
            five_minute_data.get(
                symbol,
                []
            )
        )

    if not candles:
        return None

    candle_id = candles[-1][0]

    return {
        "candle_id": candle_id,
        "rsi_1h_7": values["rsi_1h_fast"],
        "rsi_1h_14": values["rsi_1h_slow"],
        "rsi_15m_7": values["rsi_15m_fast"],
        "rsi_15m_14": values["rsi_15m_slow"],
        "rsi_5m_7": cross["current_rsi_7"],
        "rsi_5m_14": cross["current_rsi_14"]
    }


# ============================================================
# SEND SIGNAL
# ============================================================

def process_closed_5m_candle(symbol):
    signal = check_signal(symbol)

    if signal is None:
        return

    candle_id = signal["candle_id"]

    with data_lock:
        previous_alert = last_alerted_candle.get(
            symbol
        )

        if previous_alert == candle_id:
            return

        last_alerted_candle[symbol] = candle_id

    candle_time = datetime.fromtimestamp(
        candle_id / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    message = (
        "🔴 RSI SEQUENCE CONFIRMED\n\n"
        f"Coin: {symbol}\n"
        f"5M Candle: {candle_time}\n\n"
        "1H: RSI7 < RSI14\n"
        f"RSI7: {signal['rsi_1h_7']:.2f}\n"
        f"RSI14: {signal['rsi_1h_14']:.2f}\n\n"
        "15M: RSI7 < RSI14\n"
        f"RSI7: {signal['rsi_15m_7']:.2f}\n"
        f"RSI14: {signal['rsi_15m_14']:.2f}\n\n"
        "5M: FRESH CROSS BELOW RSI14\n"
        f"RSI7: {signal['rsi_5m_7']:.2f}\n"
        f"RSI14: {signal['rsi_5m_14']:.2f}"
    )

    logger.info(
        "SIGNAL: %s | 1H RSI7 %.2f < %.2f | "
        "15M RSI7 %.2f < %.2f | "
        "5M CROSS %.2f < %.2f",
        symbol,
        signal["rsi_1h_7"],
        signal["rsi_1h_14"],
        signal["rsi_15m_7"],
        signal["rsi_15m_14"],
        signal["rsi_5m_7"],
        signal["rsi_5m_14"]
    )

    send_telegram(message)


# ============================================================
# WEBSOCKET MESSAGE
# ============================================================

def on_ws_message(ws, message):
    try:
        payload = json.loads(message)

        data = payload.get("data", payload)

        if data.get("e") != "kline":
            return

        kline = data.get("k", {})

        # We only process CLOSED 5-minute candles.
        if kline.get("i") != "5m":
            return

        if not kline.get("x"):
            return

        symbol = kline.get("s")

        if not symbol:
            return

        symbol = symbol.upper()

        open_time = int(
            kline["t"]
        )

        close_price = float(
            kline["c"]
        )

        with data_lock:
            if symbol not in five_minute_data:
                five_minute_data[symbol] = deque(
                    maxlen=HISTORY_5M
                )

            candles = five_minute_data[symbol]

            # Avoid inserting same candle twice.
            if candles and candles[-1][0] == open_time:
                candles[-1] = (
                    open_time,
                    close_price
                )

            else:
                candles.append(
                    (
                        open_time,
                        close_price
                    )
                )

        # IMPORTANT:
        # Signal is checked ONLY here,
        # after a 5-minute candle closes.
        process_closed_5m_candle(symbol)

    except Exception as e:
        logger.error(
            "WebSocket message error: %s",
            e
        )


def on_ws_error(ws, error):
    logger.error(
        "WebSocket error: %s",
        error
    )


def on_ws_close(ws, close_status_code, close_msg):
    logger.warning(
        "WebSocket closed: %s | %s",
        close_status_code,
        close_msg
    )


def on_ws_open(ws):
    logger.info(
        "WebSocket connected."
    )


# ============================================================
# WEBSOCKET CONNECTION
# ============================================================

def websocket_worker(symbols, worker_number):
    streams = [
        f"{symbol.lower()}@kline_5m"
        for symbol in symbols
    ]

    stream_string = "/".join(streams)

    url = (
        f"{BINANCE_WS_URL}"
        f"?streams={stream_string}"
    )

    logger.info(
        "Starting WebSocket worker %d with %d symbols.",
        worker_number,
        len(symbols)
    )

    while True:
        try:
            ws = websocket.WebSocketApp(
                url,
                on_open=on_ws_open,
                on_message=on_ws_message,
                on_error=on_ws_error,
                on_close=on_ws_close
            )

            ws.run_forever(
                ping_interval=60,
                ping_timeout=30
            )

        except Exception as e:
            logger.error(
                "WebSocket worker %d exception: %s",
                worker_number,
                e
            )

        logger.warning(
            "WebSocket worker %d reconnecting in 10 seconds...",
            worker_number
        )

        time.sleep(10)


# ============================================================
# LOAD INITIAL HISTORY
# ============================================================

def load_history(symbols):
    total = len(symbols)

    logger.info(
        "Loading 5M history for %d symbols...",
        total
    )

    successful = 0

    for index, symbol in enumerate(symbols, start=1):

        candles = get_initial_5m_history(
            symbol
        )

        if candles:
            with data_lock:
                five_minute_data[symbol] = deque(
                    candles,
                    maxlen=HISTORY_5M
                )

            successful += 1

        if index % 25 == 0 or index == total:
            logger.info(
                "History progress: %d/%d",
                index,
                total
            )

        time.sleep(
            STARTUP_REQUEST_DELAY
        )

    logger.info(
        "History loaded: %d/%d symbols.",
        successful,
        total
    )


# ============================================================
# STARTUP
# ============================================================

def main():

    logger.info(
        "================================================"
    )
    logger.info(
        "BINANCE FUTURES RSI SEQUENCE SCANNER"
    )
    logger.info(
        "================================================"
    )

    logger.info(
        "Conditions:"
    )

    logger.info(
        "1H RSI(7) < RSI(14)"
    )

    logger.info(
        "15M RSI(7) < RSI(14)"
    )

    logger.info(
        "5M RSI(7) fresh cross below RSI(14)"
    )

    logger.info(
        "Signal checked only after 5M candle close."
    )

    logger.info(
        "No OI / Funding / Volume / Order Book / MACD / 1M."
    )

    # --------------------------------------------------------
    # GET SYMBOLS
    # --------------------------------------------------------

    symbols = get_usdt_symbols()

    if not symbols:
        logger.error(
            "No Binance USDT perpetual symbols found."
        )
        return

    # --------------------------------------------------------
    # LOAD HISTORY
    # --------------------------------------------------------

    load_history(symbols)

    # --------------------------------------------------------
    # TELEGRAM STARTUP MESSAGE
    # --------------------------------------------------------

    startup_message = (
        "🟢 RSI SCANNER IS NOW RUNNING\n\n"
        "Binance Futures USDT Perpetuals\n\n"
        "Conditions:\n"
        "1H RSI7 < RSI14\n"
        "15M RSI7 < RSI14\n"
        "5M RSI7 fresh cross below RSI14\n\n"
        "Signal check: 5M candle close only\n"
        "Duplicate alert: blocked per 5M candle\n\n"
        "Only RSI conditions are used."
    )

    send_telegram(
        startup_message
    )

    # --------------------------------------------------------
    # START WEBSOCKET WORKERS
    # --------------------------------------------------------

    chunks = [
        symbols[i:i + STREAMS_PER_CONNECTION]
        for i in range(
            0,
            len(symbols),
            STREAMS_PER_CONNECTION
        )
    ]

    logger.info(
        "Starting %d WebSocket connection(s).",
        len(chunks)
    )

    for worker_number, chunk in enumerate(
        chunks,
        start=1
    ):
        thread = threading.Thread(
            target=websocket_worker,
            args=(
                chunk,
                worker_number
            ),
            daemon=True
        )

        thread.start()

        # Small delay between connections.
        time.sleep(2)

    logger.info(
        "Scanner is running."
    )

    # Keep main process alive.
    while True:
        time.sleep(60)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
