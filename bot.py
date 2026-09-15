```python
import os
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


# ============================================================
# OI + FUNDING FUTURES SCANNER
# 1H OI + 15M OI
# FUNDING DIRECTION
# NO 5M
# ============================================================


# ============================================================
# CONFIG
# ============================================================

PIPAI_BASE = "https://api-dev.pipai.org"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Scan every 15 minutes AFTER previous scan finishes.
SCAN_INTERVAL = 15 * 60

REQUEST_TIMEOUT = 20

# Small delay between OI requests.
# PIPAI gateway allows up to 1000 requests/min.
REQUEST_DELAY = 0.075

# No volume filter.
MIN_24H_VOLUME = 0

# Safety limit.
MAX_SYMBOLS = 400


# ============================================================
# FORCE PYTHON OUTPUT TO RENDER LOGS
# ============================================================

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def log(message=""):
    print(message, flush=True)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        log("TELEGRAM ERROR: TELEGRAM_BOT_TOKEN is missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        log("TELEGRAM ERROR: TELEGRAM_CHAT_ID is missing.")
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
            timeout=15
        )

        if response.status_code == 200:

            log(
                f"TELEGRAM SENT: {message}"
            )

            return True

        log(
            f"TELEGRAM ERROR HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    except Exception as e:

        log(
            f"TELEGRAM EXCEPTION: {repr(e)}"
        )

    return False


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"OI Funding Scanner is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    try:

        port = int(
            os.environ.get(
                "PORT",
                "10000"
            )
        )

        server = HTTPServer(
            ("0.0.0.0", port),
            HealthHandler
        )

        log(
            f"HEALTH SERVER STARTED "
            f"ON PORT {port}"
        )

        server.serve_forever()

    except Exception as e:

        log(
            f"HEALTH SERVER ERROR: "
            f"{repr(e)}"
        )


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "OI-Funding-Scanner/2.0"
})


# ============================================================
# PIPAI GET
# ============================================================

def pipai_get(path, params=None):

    url = PIPAI_BASE + path

    try:

        log(
            f"API REQUEST: {path} "
            f"{params if params else ''}"
        )

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        log(
            f"API RESPONSE: "
            f"{response.status_code} "
            f"{path}"
        )

        if response.status_code != 200:

            log(
                f"API ERROR BODY: "
                f"{response.text[:500]}"
            )

            return None

        try:

            return response.json()

        except Exception:

            log(
                f"JSON ERROR: "
                f"{path}"
            )

            log(
                response.text[:500]
            )

            return None

    except Exception as e:

        log(
            f"PIPAI REQUEST EXCEPTION: "
            f"{path} | {repr(e)}"
        )

        return None


# ============================================================
# GET FUTURES SYMBOLS
# ============================================================

def get_symbols():

    log("")
    log("==========================================")
    log("GETTING FUTURES SYMBOLS")
    log("==========================================")

    data = pipai_get(
        "/ticker/24hr/active"
    )

    # --------------------------------------------------------
    # Primary source
    # --------------------------------------------------------

    if isinstance(data, list):

        log(
            f"ACTIVE TICKER ITEMS: "
            f"{len(data)}"
        )

    else:

        log(
            "ACTIVE TICKER RESPONSE "
            "IS NOT A LIST."
        )

    # --------------------------------------------------------
    # Fallback to funding list
    # --------------------------------------------------------

    if not isinstance(data, list):

        log(
            "USING FUNDING LIST "
            "AS SYMBOL FALLBACK..."
        )

        data = pipai_get(
            "/funding/rates"
        )

    if not isinstance(data, list):

        log(
            "ERROR: COULD NOT GET "
            "FUTURES SYMBOLS."
        )

        return []

    symbols = []

    for item in data:

        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).upper().strip()

        if not symbol.endswith("USDT"):
            continue

        # Skip delivery-style symbols.
        if "_" in symbol:
            continue

        # Volume.
        volume = (
            item.get("quoteVolume")
            or item.get("volume24h")
            or item.get("quote_volume")
            or 0
        )

        try:
            volume = float(volume)

        except Exception:
            volume = 0

        if volume < MIN_24H_VOLUME:
            continue

        symbols.append(
            (symbol, volume)
        )

    # Remove duplicates.
    unique = {}

    for symbol, volume in symbols:

        if symbol not in unique:
            unique[symbol] = volume

    result = sorted(
        unique.items(),
        key=lambda x: x[1],
        reverse=True
    )

    result = result[:MAX_SYMBOLS]

    symbols = [
        symbol
        for symbol, volume in result
    ]

    log(
        f"FUTURES SYMBOLS SELECTED: "
        f"{len(symbols)}"
    )

    if symbols:

        log(
            "FIRST SYMBOLS: "
            + ", ".join(symbols[:20])
        )

    return symbols


# ============================================================
# GET ALL CURRENT FUNDING
# ============================================================

def get_all_funding():

    log("")
    log("==========================================")
    log("GETTING CURRENT FUNDING RATES")
    log("==========================================")

    data = pipai_get(
        "/funding/rates"
    )

    if not isinstance(data, list):

        log(
            "ERROR: FUNDING RESPONSE "
            "IS INVALID."
        )

        return {}

    funding = {}

    for item in data:

        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).upper().strip()

        if not symbol.endswith("USDT"):
            continue

        try:

            rate = float(
                item.get(
                    "fundingRate",
                    0
                )
            )

        except Exception:

            continue

        funding[symbol] = rate

    log(
        f"FUNDING RATES RECEIVED: "
        f"{len(funding)}"
    )

    return funding


# ============================================================
# GET OI DIRECTION
# ============================================================

def get_oi_direction(
    symbol,
    period
):

    data = pipai_get(
        "/openInterestHist",
        {
            "symbol": symbol,
            "period": period,
            "limit": 2
        }
    )

    if not isinstance(data, list):

        log(
            f"{symbol} {period} OI: "
            f"NO DATA"
        )

        return None

    if len(data) < 2:

        log(
            f"{symbol} {period} OI: "
            f"LESS THAN 2 DATA POINTS"
        )

        return None

    try:

        previous = float(
            data[-2]["sumOpenInterest"]
        )

        current = float(
            data[-1]["sumOpenInterest"]
        )

    except Exception as e:

        log(
            f"{symbol} {period} OI PARSE ERROR: "
            f"{repr(e)}"
        )

        return None

    if current > previous:

        log(
            f"{symbol} {period} OI: "
            f"UP "
            f"({previous} -> {current})"
        )

        return "UP"

    if current < previous:

        log(
            f"{symbol} {period} OI: "
            f"DOWN "
            f"({previous} -> {current})"
        )

        return "DOWN"

    log(
        f"{symbol} {period} OI: FLAT"
    )

    return "FLAT"


# ============================================================
# DETERMINE LONG / SHORT
# ============================================================

def determine_direction(
    oi_direction,
    funding_rate
):

    # User strategy:
    #
    # OI UP + positive funding = LONG
    # OI UP + negative funding = SHORT
    #
    # OI DOWN / FLAT = NO SIGNAL.

    if oi_direction != "UP":
        return None

    if funding_rate > 0:
        return "LONG"

    if funding_rate < 0:
        return "SHORT"

    return None


# ============================================================
# ANALYZE ONE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    funding_rates
):

    funding = funding_rates.get(
        symbol
    )

    if funding is None:

        log(
            f"{symbol}: "
            f"NO FUNDING DATA"
        )

        return None

    log("")
    log(
        f"----- ANALYZING {symbol} -----"
    )

    # --------------------------------------------------------
    # 1H OI
    # --------------------------------------------------------

    oi_1h = get_oi_direction(
        symbol,
        "1h"
    )

    time.sleep(
        REQUEST_DELAY
    )

    # --------------------------------------------------------
    # 15M OI
    # --------------------------------------------------------

    oi_15m = get_oi_direction(
        symbol,
        "15m"
    )

    time.sleep(
        REQUEST_DELAY
    )

    if oi_1h is None:
        return None

    if oi_15m is None:
        return None

    # --------------------------------------------------------
    # Direction
    # --------------------------------------------------------

    direction_1h = determine_direction(
        oi_1h,
        funding
    )

    direction_15m = determine_direction(
        oi_15m,
        funding
    )

    log(
        f"{symbol} | "
        f"Funding={funding:.8f} | "
        f"1H={direction_1h} | "
        f"15M={direction_15m}"
    )

    # Both must have a direction.
    if direction_1h is None:
        return None

    if direction_15m is None:
        return None

    # Directions MUST match.
    if direction_1h != direction_15m:

        log(
            f"{symbol}: "
            f"DIRECTIONS DO NOT MATCH"
        )

        return None

    # --------------------------------------------------------
    # CONFIRMED SIGNAL
    # --------------------------------------------------------

    log("")
    log(
        "****************************************"
    )

    log(
        f"CONFIRMED SIGNAL: "
        f"{symbol} {direction_1h}"
    )

    log(
        "****************************************"
    )

    return {
        "symbol": symbol,
        "direction": direction_1h,
        "funding": funding,
        "oi_1h": oi_1h,
        "oi_15m": oi_15m
    }


# ============================================================
# RUN SCAN
# ============================================================

def run_scan():

    log("")
    log("")
    log("============================================================")
    log(
        "NEW SCAN: "
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )
    log("============================================================")

    # --------------------------------------------------------
    # Symbols
    # --------------------------------------------------------

    symbols = get_symbols()

    if not symbols:

        log(
            "SCAN STOPPED: "
            "NO SYMBOLS FOUND."
        )

        return []

    # --------------------------------------------------------
    # Funding
    # --------------------------------------------------------

    funding_rates = get_all_funding()

    if not funding_rates:

        log(
            "SCAN STOPPED: "
            "NO FUNDING DATA."
        )

        return []

    # --------------------------------------------------------
    # Analyze
    # --------------------------------------------------------

    signals = []

    total = len(symbols)

    log("")
    log(
        f"STARTING SYMBOL ANALYSIS: "
        f"{total} SYMBOLS"
    )
    log("")

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        log(
            f"[{index}/{total}] "
            f"CHECKING {symbol}"
        )

        try:

            result = analyze_symbol(
                symbol,
                funding_rates
            )

            if result:

                signals.append(
                    result
                )

        except Exception as e:

            log(
                f"{symbol} ANALYSIS ERROR: "
                f"{repr(e)}"
            )

    # --------------------------------------------------------
    # Scan complete
    # --------------------------------------------------------

    log("")
    log("============================================================")
    log(
        f"SCAN COMPLETE | "
        f"SIGNALS FOUND: {len(signals)}"
    )
    log("============================================================")

    return signals


# ============================================================
# ALERT DEDUPLICATION
# ============================================================

# Example:
#
# BTCUSDT LONG
# BTCUSDT LONG
# BTCUSDT LONG
#
# Only first LONG alert.
#
# If later:
#
# BTCUSDT SHORT
#
# SHORT alert will be sent.


last_alerted_direction = {}


def send_new_signals(
    signals
):

    if not signals:

        log(
            "NO NEW SIGNALS TO SEND."
        )

        return

    for signal in signals:

        symbol = signal[
            "symbol"
        ]

        direction = signal[
            "direction"
        ]

        previous = (
            last_alerted_direction.get(
                symbol
            )
        )

        # Same direction already alerted.
        if previous == direction:

            log(
                f"ALERT SKIPPED: "
                f"{symbol} {direction} "
                f"(already alerted)"
            )

            continue

        message = (
            f"{symbol} {direction}"
        )

        log(
            f"SENDING ALERT: "
            f"{message}"
        )

        sent = send_telegram(
            message
        )

        if sent:

            last_alerted_direction[
                symbol
            ] = direction


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():

    log("")
    log("============================================================")
    log("OI + FUNDING SCANNER STARTING")
    log("============================================================")

    log(
        f"PIPAI BASE: {PIPAI_BASE}"
    )

    log(
        f"SCAN INTERVAL: "
        f"{SCAN_INTERVAL // 60} MINUTES"
    )

    log(
        f"MAX SYMBOLS: "
        f"{MAX_SYMBOLS}"
    )

    log(
        "TIMEFRAMES: 1H + 15M"
    )

    log(
        "5M: DISABLED"
    )

    log("")

    # --------------------------------------------------------
    # Startup Telegram
    # --------------------------------------------------------

    log(
        "SENDING STARTUP TELEGRAM..."
    )

    send_telegram(
        "OI Funding Scanner Started"
    )

    log(
        "STARTUP COMPLETE."
    )

    # --------------------------------------------------------
    # Immediate first scan
    # --------------------------------------------------------

    first_scan = True

    while True:

        try:

            if not first_scan:

                log("")
                log(
                    f"WAITING "
                    f"{SCAN_INTERVAL // 60} "
                    f"MINUTES FOR NEXT SCAN..."
                )

                time.sleep(
                    SCAN_INTERVAL
                )

            first_scan = False

            signals = run_scan()

            send_new_signals(
                signals
            )

        except Exception as e:

            log("")
            log(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
            )

            log(
                f"MAIN LOOP ERROR: "
                f"{repr(e)}"
            )

            log(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
            )

            time.sleep(60)


# ============================================================
# MAIN
# ============================================================

def main():

    log("")
    log("============================================================")
    log("BOT.PY STARTED")
    log("============================================================")

    log(
        f"Python version: "
        f"{sys.version}"
    )

    log(
        f"Process ID: "
        f"{os.getpid()}"
    )

    # --------------------------------------------------------
    # Environment check
    # --------------------------------------------------------

    if TELEGRAM_BOT_TOKEN:

        log(
            "TELEGRAM_BOT_TOKEN: FOUND"
        )

    else:

        log(
            "TELEGRAM_BOT_TOKEN: MISSING"
        )

    if TELEGRAM_CHAT_ID:

        log(
            "TELEGRAM_CHAT_ID: FOUND"
        )

    else:

        log(
            "TELEGRAM_CHAT_ID: MISSING"
        )

    # --------------------------------------------------------
    # Health server
    # --------------------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    log(
        "HEALTH SERVER THREAD STARTED."
    )

    # Give health server a moment.
    time.sleep(1)

    # --------------------------------------------------------
    # Scanner
    # --------------------------------------------------------

    log(
        "STARTING SCANNER LOOP..."
    )

    scanner_loop()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        log(
            "BOT STOPPED BY KEYBOARD."
        )

    except Exception as e:

        log(
            "FATAL BOT ERROR:"
        )

        log(
            repr(e)
        )

        raise
```
