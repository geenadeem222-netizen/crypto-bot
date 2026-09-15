import os
import time
import threading
from datetime import datetime, timezone

import requests
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# CONFIG
# ============================================================

PIPAI_BASE = "https://api-dev.pipai.org"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SCAN_INTERVAL = 15 * 60          # 15 minutes
REQUEST_TIMEOUT = 20

# PIPAI gateway limit is 1000 requests/min.
# Keep some safety margin.
REQUEST_DELAY = 0.075

# Minimum 24h quote volume.
# Set to 0 if you want every USDT perpetual returned by PIPAI.
MIN_24H_VOLUME = 0

# Maximum symbols to process in one scan.
# 400 symbols require about 800 OI requests + funding/ticker requests.
MAX_SYMBOLS = 400


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
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
            print("Telegram sent:", message)
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        print("Telegram exception:", repr(e))

    return False


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(
            b"OI Funding Scanner is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Health server running on port {port}")

    server.serve_forever()


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "OI-Funding-Scanner/1.0"
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

        if response.status_code != 200:
            print(
                f"PIPAI HTTP {response.status_code}: "
                f"{path} {params}"
            )
            return None

        return response.json()

    except Exception as e:
        print(
            f"PIPAI request error: {path} "
            f"{repr(e)}"
        )

        return None


# ============================================================
# GET FUTURES SYMBOLS
# ============================================================

def get_symbols():

    print("Getting Futures symbols...")

    # Gateway helper.
    data = pipai_get(
        "/ticker/24hr/active"
    )

    if not isinstance(data, list):
        print("Active ticker response was not a list.")

        # Fallback to funding list.
        data = pipai_get(
            "/funding/rates"
        )

    if not isinstance(data, list):
        print("Could not get Futures symbols.")
        return []

    symbols = []

    for item in data:

        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get("symbol", "")
        ).upper()

        if not symbol.endswith("USDT"):
            continue

        # Exclude common non-perpetual / delivery-looking symbols.
        if "_" in symbol:
            continue

        # Volume filter.
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
        unique[symbol] = volume

    result = sorted(
        unique.items(),
        key=lambda x: x[1],
        reverse=True
    )

    result = result[:MAX_SYMBOLS]

    print(
        f"Futures symbols selected: {len(result)}"
    )

    return [
        symbol
        for symbol, volume in result
    ]


# ============================================================
# GET FUNDING
# ============================================================

def get_all_funding():

    print("Getting current funding rates...")

    data = pipai_get(
        "/funding/rates"
    )

    if not isinstance(data, list):
        print("Funding response invalid.")
        return {}

    funding = {}

    for item in data:

        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get("symbol", "")
        ).upper()

        if not symbol.endswith("USDT"):
            continue

        try:
            rate = float(
                item.get("fundingRate", 0)
            )
        except Exception:
            continue

        funding[symbol] = rate

    print(
        f"Funding rates received: {len(funding)}"
    )

    return funding


# ============================================================
# GET HISTORICAL OI
# ============================================================

def get_oi_direction(symbol, period):

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

    except Exception:
        return None

    if current > previous:
        return "UP"

    if current < previous:
        return "DOWN"

    return "FLAT"


# ============================================================
# DETERMINE DIRECTION
# ============================================================

def determine_direction(oi_direction, funding_rate):

    # User's requested strategy:
    #
    # OI UP + positive funding = LONG
    # OI UP + negative funding = SHORT
    #
    # OI DOWN = no signal.

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

def analyze_symbol(symbol, funding_rates):

    funding = funding_rates.get(symbol)

    if funding is None:
        return None

    # -------------------------
    # 1H OI
    # -------------------------

    oi_1h = get_oi_direction(
        symbol,
        "1h"
    )

    time.sleep(REQUEST_DELAY)

    # -------------------------
    # 15M OI
    # -------------------------

    oi_15m = get_oi_direction(
        symbol,
        "15m"
    )

    time.sleep(REQUEST_DELAY)

    if oi_1h is None or oi_15m is None:
        return None

    direction_1h = determine_direction(
        oi_1h,
        funding
    )

    direction_15m = determine_direction(
        oi_15m,
        funding
    )

    # Both timeframes must produce
    # a direction.
    if direction_1h is None:
        return None

    if direction_15m is None:
        return None

    # 1H and 15M direction MUST match.
    if direction_1h != direction_15m:
        return None

    return {
        "symbol": symbol,
        "direction": direction_1h,
        "funding": funding,
        "oi_1h": oi_1h,
        "oi_15m": oi_15m
    }


# ============================================================
# SCAN
# ============================================================

def run_scan():

    print()
    print("=" * 70)
    print(
        "NEW SCAN:",
        datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )
    print("=" * 70)

    symbols = get_symbols()

    if not symbols:
        print("No symbols found.")
        return

    funding_rates = get_all_funding()

    if not funding_rates:
        print("No funding data.")
        return

    signals = []

    total = len(symbols)

    for index, symbol in enumerate(symbols, start=1):

        print(
            f"[{index}/{total}] Checking {symbol}"
        )

        result = analyze_symbol(
            symbol,
            funding_rates
        )

        if result:
            signals.append(result)

            print(
                f"*** SIGNAL: "
                f"{result['symbol']} "
                f"{result['direction']} ***"
            )

    print()
    print(
        f"Scan complete. Signals found: "
        f"{len(signals)}"
    )

    return signals


# ============================================================
# ALERT DEDUPLICATION
# ============================================================

# Stores the last alerted direction for each symbol.
#
# Example:
# BTCUSDT LONG -> alert
# BTCUSDT LONG -> no repeat
# BTCUSDT LONG -> no repeat
# BTCUSDT SHORT -> new alert
#
last_alerted_direction = {}


def send_new_signals(signals):

    if not signals:
        return

    for signal in signals:

        symbol = signal["symbol"]
        direction = signal["direction"]

        previous = last_alerted_direction.get(
            symbol
        )

        # Same direction already alerted.
        if previous == direction:
            continue

        message = (
            f"{symbol} {direction}"
        )

        if send_telegram(message):
            last_alerted_direction[
                symbol
            ] = direction


# ============================================================
# MAIN LOOP
# ============================================================

def scanner_loop():

    print("=" * 70)
    print("OI + FUNDING SCANNER STARTING")
    print("=" * 70)

    send_telegram(
        "OI Funding Scanner Started"
    )

    first_scan = True

    while True:

        try:

            if not first_scan:
                print(
                    f"\nWaiting {SCAN_INTERVAL // 60} "
                    f"minutes for next scan..."
                )

                time.sleep(
                    SCAN_INTERVAL
                )

            first_scan = False

            signals = run_scan()

            if signals:
                send_new_signals(
                    signals
                )

        except Exception as e:

            print(
                "MAIN LOOP ERROR:",
                repr(e)
            )

            time.sleep(60)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    # Start Render health server.
    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # Start scanner.
    scanner_loop()
