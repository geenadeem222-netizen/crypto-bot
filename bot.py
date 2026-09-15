import os
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


# ============================================================
# CONFIG
# ============================================================

PIPAI_BASE = "https://api-dev.pipai.org"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SCAN_INTERVAL = 15 * 60
REQUEST_TIMEOUT = 20

# PIPAI gateway safety delay
REQUEST_DELAY = 0.075

# No volume restriction
MIN_24H_VOLUME = 0

# Maximum symbols per scan
MAX_SYMBOLS = 400


# ============================================================
# FORCE LIVE RENDER LOGS
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
        log("TELEGRAM ERROR: BOT TOKEN MISSING")
        return False

    if not TELEGRAM_CHAT_ID:
        log("TELEGRAM ERROR: CHAT ID MISSING")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=15
        )

        if response.status_code == 200:

            log(
                f"TELEGRAM SENT: {message}"
            )

            return True

        log(
            f"TELEGRAM ERROR "
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
            f"HEALTH SERVER STARTED ON PORT {port}"
        )

        server.serve_forever()

    except Exception as e:

        log(
            f"HEALTH SERVER ERROR: {repr(e)}"
        )


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "OI-Funding-Scanner/3.0",
    "Accept": "application/json"
})


# ============================================================
# PIPAI REQUEST
# ============================================================

def pipai_get(path, params=None):

    url = PIPAI_BASE + path

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        log(
            f"API {response.status_code}: "
            f"{path} "
            f"{params if params else ''}"
        )

        if response.status_code != 200:

            log(
                f"API ERROR: "
                f"{response.text[:300]}"
            )

            return None

        try:
            return response.json()

        except Exception:

            log(
                f"JSON PARSE ERROR: {path}"
            )

            return None

    except Exception as e:

        log(
            f"REQUEST ERROR {path}: "
            f"{repr(e)}"
        )

        return None


# ============================================================
# SYMBOL NORMALIZER
# ============================================================

def normalize_symbol(value):

    if value is None:
        return ""

    symbol = str(value).upper().strip()

    # Remove common separators
    symbol = symbol.replace(
        "/",
        ""
    )

    symbol = symbol.replace(
        "-",
        ""
    )

    symbol = symbol.replace(
        ":",
        ""
    )

    return symbol


# ============================================================
# EXTRACT FUNDING RATE
# ============================================================

def extract_funding_rate(item):

    if not isinstance(item, dict):
        return None

    # Possible field names returned by different gateways.
    fields = [
        "fundingRate",
        "funding_rate",
        "lastFundingRate",
        "lastFundingRateValue",
        "rate",
        "funding"
    ]

    for field in fields:

        value = item.get(field)

        if value is None:
            continue

        # Ignore empty strings
        if value == "":
            continue

        try:

            rate = float(value)

            return rate

        except Exception:

            continue

    return None


# ============================================================
# GET SYMBOLS
# ============================================================

def get_symbols():

    log("")
    log("==========================================")
    log("GETTING FUTURES SYMBOLS")
    log("==========================================")

    data = pipai_get(
        "/ticker/24hr/active"
    )

    if not isinstance(data, list):

        log(
            "ACTIVE TICKER NOT USABLE."
        )

        log(
            "USING FUNDING LIST FOR SYMBOLS..."
        )

        data = pipai_get(
            "/funding/rates"
        )

    if not isinstance(data, list):

        log(
            "ERROR: NO SYMBOL LIST"
        )

        return []

    unique = {}

    for item in data:

        if not isinstance(item, dict):
            continue

        raw_symbol = (
            item.get("symbol")
            or item.get("s")
        )

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol.endswith("USDT"):
            continue

        # Ignore delivery contracts
        if "_" in symbol:
            continue

        # Ignore obviously invalid names
        if len(symbol) < 6:
            continue

        volume = (
            item.get("quoteVolume")
            or item.get("volume24h")
            or item.get("quote_volume")
            or item.get("quoteVol")
            or 0
        )

        try:
            volume = float(volume)
        except Exception:
            volume = 0.0

        if volume < MIN_24H_VOLUME:
            continue

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
            "SYMBOL SAMPLE: "
            + ", ".join(symbols[:20])
        )

    return symbols


# ============================================================
# GET ALL FUNDING
# ============================================================

def get_all_funding():

    log("")
    log("==========================================")
    log("GETTING FUNDING RATES")
    log("==========================================")

    data = pipai_get(
        "/funding/rates"
    )

    if not isinstance(data, list):

        log(
            "FUNDING LIST INVALID"
        )

        return {}

    funding = {}

    positive = 0
    negative = 0
    zero = 0

    for item in data:

        if not isinstance(item, dict):
            continue

        raw_symbol = (
            item.get("symbol")
            or item.get("s")
        )

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol.endswith("USDT"):
            continue

        rate = extract_funding_rate(
            item
        )

        if rate is None:
            continue

        funding[symbol] = rate

        if rate > 0:
            positive += 1

        elif rate < 0:
            negative += 1

        else:
            zero += 1

    log(
        f"FUNDING RECEIVED: "
        f"{len(funding)}"
    )

    log(
        f"POSITIVE: {positive} | "
        f"NEGATIVE: {negative} | "
        f"ZERO: {zero}"
    )

    # Show real examples
    shown = 0

    for symbol, rate in funding.items():

        if rate != 0:

            log(
                f"FUNDING SAMPLE: "
                f"{symbol} = {rate:.10f}"
            )

            shown += 1

            if shown >= 10:
                break

    if positive == 0 and negative == 0:

        log(
            "WARNING: ALL FUNDING VALUES "
            "ARE ZERO."
        )

        log(
            "PER-SYMBOL FUNDING FALLBACK "
            "WILL BE USED."
        )

    return funding


# ============================================================
# PER SYMBOL FUNDING FALLBACK
# ============================================================

def get_symbol_funding(symbol):

    # Historical funding endpoint.
    data = pipai_get(
        f"/funding/rates/{symbol}/history"
    )

    if not isinstance(data, list):
        return None

    if not data:
        return None

    # Usually newest item is last.
    candidates = reversed(data)

    for item in candidates:

        rate = extract_funding_rate(
            item
        )

        if rate is not None:

            return rate

    return None


# ============================================================
# GET FINAL FUNDING
# ============================================================

def get_funding_for_symbol(
    symbol,
    funding_map
):

    # First use all-funding response.
    if symbol in funding_map:

        rate = funding_map[symbol]

        # If it is non-zero, use it immediately.
        if rate != 0:

            return rate

    # If missing/zero, verify directly.
    log(
        f"{symbol}: "
        f"CHECKING INDIVIDUAL FUNDING"
    )

    time.sleep(
        REQUEST_DELAY
    )

    rate = get_symbol_funding(
        symbol
    )

    if rate is not None:

        log(
            f"{symbol}: "
            f"INDIVIDUAL FUNDING = "
            f"{rate:.10f}"
        )

        return rate

    # If individual endpoint fails,
    # return the original value if available.
    if symbol in funding_map:

        return funding_map[symbol]

    return None


# ============================================================
# OPEN INTEREST DIRECTION
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
        return None

    if len(data) < 2:
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
            f"{symbol} {period} OI ERROR: "
            f"{repr(e)}"
        )

        return None

    if current > previous:

        log(
            f"{symbol} {period} OI UP "
            f"({previous} -> {current})"
        )

        return "UP"

    if current < previous:

        log(
            f"{symbol} {period} OI DOWN "
            f"({previous} -> {current})"
        )

        return "DOWN"

    log(
        f"{symbol} {period} OI FLAT"
    )

    return "FLAT"


# ============================================================
# DIRECTION
# ============================================================

def determine_direction(
    oi_direction,
    funding
):

    if oi_direction != "UP":
        return None

    if funding > 0:
        return "LONG"

    if funding < 0:
        return "SHORT"

    return None


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    funding_map
):

    funding = get_funding_for_symbol(
        symbol,
        funding_map
    )

    if funding is None:

        log(
            f"{symbol}: "
            f"NO FUNDING DATA"
        )

        return None

    log("")
    log(
        f"----- {symbol} -----"
    )

    # 1H
    oi_1h = get_oi_direction(
        symbol,
        "1h"
    )

    time.sleep(
        REQUEST_DELAY
    )

    # 15M
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
        f"Funding={funding:.10f} | "
        f"1H={direction_1h} | "
        f"15M={direction_15m}"
    )

    # Both directions required
    if direction_1h is None:
        return None

    if direction_15m is None:
        return None

    # Must match
    if direction_1h != direction_15m:

        log(
            f"{symbol}: "
            f"DIRECTION MISMATCH"
        )

        return None

    # Confirmed
    log("")
    log(
        "****************************************"
    )

    log(
        f"CONFIRMED: "
        f"{symbol} {direction_1h}"
    )

    log(
        "****************************************"
    )

    return {
        "symbol": symbol,
        "direction": direction_1h,
        "funding": funding
    }


# ============================================================
# SCAN
# ============================================================

def run_scan():

    log("")
    log("")
    log(
        "============================================================"
    )

    log(
        "NEW SCAN: "
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )

    log(
        "============================================================"
    )

    symbols = get_symbols()

    if not symbols:

        log(
            "NO SYMBOLS FOUND."
        )

        return []

    funding_map = get_all_funding()

    signals = []

    total = len(symbols)

    log("")
    log(
        f"STARTING {total} SYMBOL SCAN"
    )

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
                funding_map
            )

            if result:

                signals.append(
                    result
                )

        except Exception as e:

            log(
                f"{symbol} ERROR: "
                f"{repr(e)}"
            )

    log("")
    log(
        "============================================================"
    )

    log(
        f"SCAN COMPLETE | "
        f"SIGNALS FOUND: {len(signals)}"
    )

    log(
        "============================================================"
    )

    return signals


# ============================================================
# ALERT MEMORY
# ============================================================

last_alerted_direction = {}


def send_new_signals(
    signals
):

    if not signals:

        log(
            "NO NEW SIGNALS."
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

        if previous == direction:

            log(
                f"SKIP DUPLICATE: "
                f"{symbol} {direction}"
            )

            continue

        message = (
            f"{symbol} {direction}"
        )

        if send_telegram(
            message
        ):

            last_alerted_direction[
                symbol
            ] = direction


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():

    log("")
    log(
        "============================================================"
    )

    log(
        "OI + FUNDING SCANNER STARTING"
    )

    log(
        "TIMEFRAMES: 1H + 15M"
    )

    log(
        "5M: DISABLED"
    )

    log(
        "============================================================"
    )

    # Startup alert
    send_telegram(
        "OI Funding Scanner Started"
    )

    first_scan = True

    while True:

        try:

            if not first_scan:

                log("")
                log(
                    "WAITING 15 MINUTES..."
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

            log(
                f"MAIN LOOP ERROR: "
                f"{repr(e)}"
            )

            time.sleep(60)


# ============================================================
# MAIN
# ============================================================

def main():

    log("")
    log(
        "============================================================"
    )

    log(
        "BOT.PY STARTED"
    )

    log(
        f"PYTHON: {sys.version}"
    )

    log(
        "============================================================"
    )

    # Environment
    log(
        "TELEGRAM TOKEN: "
        + (
            "FOUND"
            if TELEGRAM_BOT_TOKEN
            else "MISSING"
        )
    )

    log(
        "TELEGRAM CHAT ID: "
        + (
            "FOUND"
            if TELEGRAM_CHAT_ID
            else "MISSING"
        )
    )

    # Health server
    thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    thread.start()

    time.sleep(1)

    # Scanner
    scanner_loop()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        log(
            f"FATAL ERROR: {repr(e)}"
        )

        raise
