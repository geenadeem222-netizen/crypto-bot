import os
import json
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


# ============================================================
# BINANCE FUTURES OI + FUNDING SCANNER
# 1H OI + FUNDING
# 15M OI + FUNDING
# 5M PRICE CONFIRMATION
# RENDER READY
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

SCAN_INTERVAL_SECONDS = 300          # 5 minutes
CONFIRM_SECONDS = 15                 # 15 second confirmation
REQUEST_DELAY_SECONDS = 0.12

REQUEST_TIMEOUT = 15
MAX_RETRIES = 2

STATE_FILE = "alert_state.json"
SNAPSHOT_FILE = "oi_snapshots.json"

# Minimum OI change percentage.
# 0.0 = any directional change is accepted.
MIN_OI_CHANGE_PERCENT = 0.0

# How long to keep historical snapshots
SNAPSHOT_KEEP_SECONDS = 2 * 60 * 60  # 2 hours


# ============================================================
# BINANCE ENDPOINTS
# ============================================================

BINANCE_BASE_URLS = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
]


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 Binance-OI-Funding-Scanner/1.0",
    "Accept": "application/json",
    "Connection": "keep-alive",
})


# ============================================================
# GLOBALS
# ============================================================

current_base_url_index = 0

symbols_cache = []
symbols_cache_time = 0

funding_cache = {}
funding_cache_time = 0

oi_snapshots = []

alert_state = {}

rate_limit_until = 0


# ============================================================
# PRINT
# ============================================================

def log(message=""):
    print(message, flush=True)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_text():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OI Funding Scanner is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(("0.0.0.0", port), HealthHandler)

    log(f"Health server listening on port {port}")

    server.serve_forever()


# ============================================================
# BINANCE EXCEPTIONS
# ============================================================

class BinanceRateLimitError(Exception):
    pass


class BinanceRestrictedError(Exception):
    pass


# ============================================================
# BINANCE REQUEST
# ============================================================

def binance_get(path, params=None):

    global current_base_url_index
    global rate_limit_until

    now = time.time()

    if now < rate_limit_until:
        raise BinanceRateLimitError(
            "Binance cooldown active"
        )

    last_error = None

    for attempt in range(MAX_RETRIES + 1):

        for offset in range(len(BINANCE_BASE_URLS)):

            index = (
                current_base_url_index + offset
            ) % len(BINANCE_BASE_URLS)

            base_url = BINANCE_BASE_URLS[index]

            url = base_url + path

            try:

                response = SESSION.get(
                    url,
                    params=params,
                    timeout=REQUEST_TIMEOUT
                )

                # ------------------------------------------------
                # RATE LIMIT / BAN
                # ------------------------------------------------

                if response.status_code in (418, 429):

                    current_base_url_index = (
                        index + 1
                    ) % len(BINANCE_BASE_URLS)

                    last_error = BinanceRateLimitError(
                        f"HTTP {response.status_code}"
                    )

                    continue

                # ------------------------------------------------
                # RESTRICTED LOCATION
                # ------------------------------------------------

                if response.status_code == 451:

                    raise BinanceRestrictedError(
                        "Binance restricted location HTTP 451"
                    )

                response.raise_for_status()

                current_base_url_index = index

                return response.json()

            except BinanceRestrictedError:
                raise

            except BinanceRateLimitError:
                continue

            except requests.RequestException as e:

                last_error = e

                continue

        time.sleep(2)

    # ------------------------------------------------------------
    # RATE LIMIT COOLDOWN
    # ------------------------------------------------------------

    if isinstance(last_error, BinanceRateLimitError):

        rate_limit_until = time.time() + 120

        raise BinanceRateLimitError(
            "Binance HTTP 418/429 rate limit"
        )

    raise RuntimeError(
        f"Binance request failed: {last_error}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        log("Telegram token missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        log("Telegram chat ID missing.")
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

        response = SESSION.post(
            url,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        log(f"Telegram sent: {message}")

        return True

    except Exception as e:

        log(f"Telegram error: {e}")

        return False


# ============================================================
# LOAD ALERT STATE
# ============================================================

def load_alert_state():

    global alert_state

    try:

        if os.path.exists(STATE_FILE):

            with open(
                STATE_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                alert_state = json.load(f)

        else:

            alert_state = {}

    except Exception as e:

        log(f"State load error: {e}")

        alert_state = {}


# ============================================================
# SAVE ALERT STATE
# ============================================================

def save_alert_state():

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                alert_state,
                f,
                indent=2
            )

    except Exception as e:

        log(f"State save error: {e}")


# ============================================================
# LOAD OI SNAPSHOTS
# ============================================================

def load_snapshots():

    global oi_snapshots

    try:

        if os.path.exists(SNAPSHOT_FILE):

            with open(
                SNAPSHOT_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                oi_snapshots = json.load(f)

        else:

            oi_snapshots = []

    except Exception as e:

        log(f"Snapshot load error: {e}")

        oi_snapshots = []


# ============================================================
# SAVE OI SNAPSHOTS
# ============================================================

def save_snapshots():

    try:

        with open(
            SNAPSHOT_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                oi_snapshots,
                f
            )

    except Exception as e:

        log(f"Snapshot save error: {e}")


# ============================================================
# GET FUTURES SYMBOLS
# ============================================================

def get_symbols():

    global symbols_cache
    global symbols_cache_time

    now = time.time()

    if (
        symbols_cache
        and now - symbols_cache_time < 3600
    ):
        return symbols_cache

    data = binance_get(
        "/fapi/v1/exchangeInfo"
    )

    result = []

    for symbol_info in data.get("symbols", []):

        symbol = symbol_info.get("symbol", "")

        contract_type = symbol_info.get(
            "contractType"
        )

        quote_asset = symbol_info.get(
            "quoteAsset"
        )

        status = symbol_info.get(
            "status"
        )

        if (
            quote_asset == "USDT"
            and contract_type == "PERPETUAL"
            and status == "TRADING"
        ):

            result.append(symbol)

    result.sort()

    symbols_cache = result
    symbols_cache_time = now

    return result


# ============================================================
# GET ALL FUNDING RATES
# ============================================================

def get_funding_rates():

    global funding_cache
    global funding_cache_time

    now = time.time()

    if (
        funding_cache
        and now - funding_cache_time < 60
    ):
        return funding_cache

    data = binance_get(
        "/fapi/v1/premiumIndex"
    )

    result = {}

    for item in data:

        symbol = item.get("symbol")

        funding_rate = item.get(
            "lastFundingRate"
        )

        if symbol and funding_rate is not None:

            try:

                result[symbol] = float(
                    funding_rate
                )

            except Exception:
                pass

    funding_cache = result
    funding_cache_time = now

    return result


# ============================================================
# GET CURRENT OPEN INTEREST
# ============================================================

def get_current_oi(symbol):

    data = binance_get(
        "/fapi/v1/openInterest",
        params={
            "symbol": symbol
        }
    )

    value = data.get("openInterest")

    if value is None:
        return None

    return float(value)


# ============================================================
# GET 5M CURRENT CANDLE
# ============================================================

def get_5m_candle(symbol):

    data = binance_get(
        "/fapi/v1/klines",
        params={
            "symbol": symbol,
            "interval": "5m",
            "limit": 1
        }
    )

    if not data:
        return None

    candle = data[0]

    return {
        "open_time": int(candle[0]),
        "open": float(candle[1]),
        "high": float(candle[2]),
        "low": float(candle[3]),
        "close": float(candle[4]),
        "volume": float(candle[5])
    }


# ============================================================
# OI SNAPSHOT HELPERS
# ============================================================

def add_oi_snapshot(snapshot):

    global oi_snapshots

    oi_snapshots.append(snapshot)

    cutoff = time.time() - SNAPSHOT_KEEP_SECONDS

    oi_snapshots = [
        x for x in oi_snapshots
        if x.get("time", 0) >= cutoff
    ]

    save_snapshots()


def get_old_oi(symbol, seconds_back):

    if not oi_snapshots:
        return None

    target = time.time() - seconds_back

    best = None
    best_distance = None

    for snapshot in oi_snapshots:

        snapshot_time = snapshot.get(
            "time",
            0
        )

        if snapshot_time > target:
            continue

        oi_value = snapshot.get(
            "oi",
            {}
        ).get(symbol)

        if oi_value is None:
            continue

        distance = abs(
            snapshot_time - target
        )

        if (
            best_distance is None
            or distance < best_distance
        ):

            best = oi_value
            best_distance = distance

    return best


# ============================================================
# OI DIRECTION
# ============================================================

def calculate_oi_direction(
    current_oi,
    old_oi
):

    if current_oi is None or old_oi is None:
        return None

    if old_oi == 0:
        return None

    change_percent = (
        (current_oi - old_oi)
        / old_oi
    ) * 100

    if abs(change_percent) < MIN_OI_CHANGE_PERCENT:
        return None

    if change_percent > 0:
        return "LONG"

    if change_percent < 0:
        return "SHORT"

    return None


# ============================================================
# FUNDING DIRECTION
# ============================================================

def funding_direction(rate):

    if rate is None:
        return None

    if rate > 0:
        return "LONG"

    if rate < 0:
        return "SHORT"

    return None


# ============================================================
# 5M PRICE DIRECTION
# ============================================================

def candle_direction(candle):

    if candle is None:
        return None

    if candle["close"] > candle["open"]:
        return "LONG"

    if candle["close"] < candle["open"]:
        return "SHORT"

    return None


# ============================================================
# DUPLICATE PROTECTION
# ============================================================

def already_alerted(
    symbol,
    direction,
    candle_open_time
):

    key = (
        f"{symbol}_"
        f"{direction}_"
        f"{candle_open_time}"
    )

    return key in alert_state


def mark_alerted(
    symbol,
    direction,
    candle_open_time
):

    key = (
        f"{symbol}_"
        f"{direction}_"
        f"{candle_open_time}"
    )

    alert_state[key] = int(time.time())

    # Keep state file small
    cutoff = time.time() - 24 * 60 * 60

    old_keys = []

    for key, value in alert_state.items():

        if value < cutoff:
            old_keys.append(key)

    for key in old_keys:
        del alert_state[key]

    save_alert_state()


# ============================================================
# TAKE OI SNAPSHOT
# ============================================================

def collect_oi_snapshot(symbols):

    snapshot = {
        "time": time.time(),
        "oi": {}
    }

    total = len(symbols)

    successful = 0

    log(
        f"Collecting OI for {total} futures symbols..."
    )

    for index, symbol in enumerate(symbols, start=1):

        try:

            oi = get_current_oi(symbol)

            if oi is not None:

                snapshot["oi"][symbol] = oi
                successful += 1

        except BinanceRateLimitError:
            raise

        except Exception as e:

            log(
                f"OI error {symbol}: {e}"
            )

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

        if index % 50 == 0:

            log(
                f"OI progress: "
                f"{index}/{total}"
            )

    log(
        f"OI snapshot complete: "
        f"{successful}/{total}"
    )

    return snapshot


# ============================================================
# FIND SIGNALS
# ============================================================

def find_signals(
    symbols,
    current_snapshot,
    funding_rates
):

    signals = []

    current_oi_data = current_snapshot["oi"]

    for symbol in symbols:

        current_oi = current_oi_data.get(
            symbol
        )

        if current_oi is None:
            continue

        # ----------------------------------------------------
        # 15 MIN OI
        # ----------------------------------------------------

        old_15m = get_old_oi(
            symbol,
            15 * 60
        )

        oi_15m_direction = calculate_oi_direction(
            current_oi,
            old_15m
        )

        if oi_15m_direction is None:
            continue

        # ----------------------------------------------------
        # 1 HOUR OI
        # ----------------------------------------------------

        old_1h = get_old_oi(
            symbol,
            60 * 60
        )

        oi_1h_direction = calculate_oi_direction(
            current_oi,
            old_1h
        )

        if oi_1h_direction is None:
            continue

        # ----------------------------------------------------
        # FUNDING
        # ----------------------------------------------------

        rate = funding_rates.get(
            symbol
        )

        funding_dir = funding_direction(
            rate
        )

        if funding_dir is None:
            continue

        # ----------------------------------------------------
        # 1H OI MUST MATCH FUNDING
        # ----------------------------------------------------

        if oi_1h_direction != funding_dir:
            continue

        # ----------------------------------------------------
        # 15M OI MUST MATCH FUNDING
        # ----------------------------------------------------

        if oi_15m_direction != funding_dir:
            continue

        # ----------------------------------------------------
        # 1H AND 15M MUST MATCH
        # ----------------------------------------------------

        if oi_1h_direction != oi_15m_direction:
            continue

        # ----------------------------------------------------
        # FINAL DIRECTION
        # ----------------------------------------------------

        final_direction = funding_dir

        signals.append({
            "symbol": symbol,
            "direction": final_direction,
            "funding": rate
        })

    return signals


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_signal(signal):

    symbol = signal["symbol"]
    direction = signal["direction"]

    try:

        first_candle = get_5m_candle(
            symbol
        )

        if first_candle is None:
            return None

        candle_open_time = first_candle[
            "open_time"
        ]

        # ----------------------------------------------------
        # WAIT 15 SECONDS
        # ----------------------------------------------------

        time.sleep(
            CONFIRM_SECONDS
        )

        second_candle = get_5m_candle(
            symbol
        )

        if second_candle is None:
            return None

        # ----------------------------------------------------
        # MUST BE SAME 5M CANDLE
        # ----------------------------------------------------

        if (
            second_candle["open_time"]
            != candle_open_time
        ):

            return None

        # ----------------------------------------------------
        # PRICE DIRECTION
        # ----------------------------------------------------

        price_dir = candle_direction(
            second_candle
        )

        if price_dir != direction:
            return None

        # ----------------------------------------------------
        # DUPLICATE CHECK
        # ----------------------------------------------------

        if already_alerted(
            symbol,
            direction,
            candle_open_time
        ):

            return None

        return {
            "symbol": symbol,
            "direction": direction,
            "candle_open_time": candle_open_time
        }

    except BinanceRateLimitError:
        raise

    except Exception as e:

        log(
            f"Confirmation error "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# PROCESS SIGNALS
# ============================================================

def process_signals(signals):

    if not signals:

        log(
            "No 1H + 15M OI/Funding matches."
        )

        return

    log(
        f"Potential matches: "
        f"{len(signals)}"
    )

    for signal in signals:

        try:

            confirmed = confirm_signal(
                signal
            )

            if confirmed is None:
                continue

            symbol = confirmed[
                "symbol"
            ]

            direction = confirmed[
                "direction"
            ]

            candle_open_time = confirmed[
                "candle_open_time"
            ]

            # ------------------------------------------------
            # USER REQUESTED ALERT FORMAT
            # ------------------------------------------------

            message = (
                f"{symbol} {direction}"
            )

            if send_telegram(message):

                mark_alerted(
                    symbol,
                    direction,
                    candle_open_time
                )

                log(
                    f"ALERT SENT: "
                    f"{message}"
                )

        except BinanceRateLimitError:
            raise

        except Exception as e:

            log(
                f"Signal processing error: {e}"
            )


# ============================================================
# ONE FULL SCAN
# ============================================================

def run_scan():

    global rate_limit_until

    log("")
    log("======================================")
    log(
        f"NEW SCAN: {utc_text()}"
    )
    log("======================================")

    # --------------------------------------------------------
    # Check cooldown
    # --------------------------------------------------------

    if time.time() < rate_limit_until:

        remaining = int(
            rate_limit_until - time.time()
        )

        log(
            f"Binance cooldown active: "
            f"{remaining}s remaining"
        )

        return

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    symbols = get_symbols()

    if not symbols:

        log(
            "No futures symbols received."
        )

        return

    log(
        f"USDT PERPETUALS: "
        f"{len(symbols)}"
    )

    # --------------------------------------------------------
    # FUNDING
    # One bulk request
    # --------------------------------------------------------

    funding_rates = get_funding_rates()

    log(
        f"Funding symbols: "
        f"{len(funding_rates)}"
    )

    # --------------------------------------------------------
    # CURRENT OI
    # One request per symbol
    # --------------------------------------------------------

    snapshot = collect_oi_snapshot(
        symbols
    )

    # --------------------------------------------------------
    # Save snapshot
    # --------------------------------------------------------

    add_oi_snapshot(
        snapshot
    )

    # --------------------------------------------------------
    # Need historical data
    # --------------------------------------------------------

    if len(oi_snapshots) < 2:

        log(
            "First OI snapshot saved."
        )

        log(
            "Waiting for future snapshots "
            "to calculate 15M/1H OI direction."
        )

        return

    # --------------------------------------------------------
    # FIND MATCHES
    # --------------------------------------------------------

    signals = find_signals(
        symbols,
        snapshot,
        funding_rates
    )

    log(
        f"1H + 15M OI/Funding matches: "
        f"{len(signals)}"
    )

    # --------------------------------------------------------
    # 5M CONFIRMATION
    # --------------------------------------------------------

    process_signals(
        signals
    )


# ============================================================
# MAIN LOOP
# ============================================================

def scanner_loop():

    log("Scanner loop started.")

    while True:

        cycle_start = time.time()

        try:

            run_scan()

        except BinanceRateLimitError as e:

            rate_limit_until = (
                time.time() + 120
            )

            log(
                "Binance rate limit: "
                f"{e}"
            )

            log(
                "Entering 120 second cooldown."
            )

        except BinanceRestrictedError as e:

            log(
                f"Binance restricted: {e}"
            )

            log(
                "Waiting before next scan."
            )

            time.sleep(300)

        except Exception as e:

            log(
                f"Scanner error: {e}"
            )

        # ----------------------------------------------------
        # Maintain 5-minute scan interval
        # ----------------------------------------------------

        elapsed = time.time() - cycle_start

        wait_time = max(
            0,
            SCAN_INTERVAL_SECONDS - elapsed
        )

        log(
            f"Next full scan in "
            f"{wait_time:.1f}s"
        )

        time.sleep(
            wait_time
        )


# ============================================================
# MAIN
# ============================================================

def main():

    log("")
    log("======================================")
    log("BINANCE FUTURES OI + FUNDING SCANNER")
    log("1H OI + FUNDING")
    log("15M OI + FUNDING")
    log("5M PRICE CONFIRMATION")
    log("======================================")
    log("")
    log("Starting...")

    # --------------------------------------------------------
    # Load local files
    # --------------------------------------------------------

    load_alert_state()
    load_snapshots()

    # --------------------------------------------------------
    # Health server
    # --------------------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    time.sleep(1)

    # --------------------------------------------------------
    # Telegram startup
    # --------------------------------------------------------

    if send_telegram(
        "OI Funding Scanner Started"
    ):

        log(
            "Telegram startup notification sent."
        )

    else:

        log(
            "Telegram startup notification FAILED."
        )

    # --------------------------------------------------------
    # Scanner
    # --------------------------------------------------------

    scanner_loop()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
