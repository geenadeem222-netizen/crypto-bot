```python
import requests
import time
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone


# ============================================================
# BINANCE FUTURES OI + FUNDING MULTI-TIMEFRAME SCANNER
#
# STRATEGY
# 1H  : OI direction == Funding direction
# 15M : OI direction == Funding direction
# 1H direction must equal 15M direction
# 5M  : current candle must move in the same direction
#
# ONE ALERT PER 5M SETUP
# RENDER HEALTH SERVER INCLUDED
# ============================================================


# ============================================================
# BINANCE ENDPOINTS
# ============================================================

BASE_URLS = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
]


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 OI-Funding-Scanner/2.0",
    "Accept": "application/json",
    "Connection": "keep-alive",
})


# ============================================================
# TELEGRAM ENVIRONMENT VARIABLES
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# SETTINGS
# ============================================================

# Full OI scan every 5 minutes.
# This is intentionally not 30 seconds because 500+ coins
# can generate too many Binance API requests.
SCAN_INTERVAL_SECONDS = 300

# Delay between OI requests.
# Keeps Binance request rate controlled.
REQUEST_DELAY_SECONDS = 0.15

# Wait after current 5M candle starts.
FIVE_MIN_CONFIRM_SECONDS = 15

# Minimum OI movement.
# 0.0 means any positive/negative change qualifies.
MIN_OI_CHANGE_PERCENT = 0.0

# Persistent duplicate state.
STATE_FILE = "alert_state.json"

# Cache symbols for this long.
SYMBOL_CACHE_SECONDS = 3600

# Funding data cache.
FUNDING_CACHE_SECONDS = 60

# HTTP timeout.
REQUEST_TIMEOUT = 15

# Number of normal request retries.
MAX_RETRIES = 2

# If Binance returns 418/429, wait this long.
RATE_LIMIT_COOLDOWN = 120


# ============================================================
# GLOBALS
# ============================================================

CURRENT_BASE_URL = BASE_URLS[0]

SYMBOL_CACHE = []

SYMBOL_CACHE_TIME = 0

FUNDING_CACHE = {}

FUNDING_CACHE_TIME = 0


# ============================================================
# CUSTOM EXCEPTION
# ============================================================

class BinanceRateLimitError(Exception):
    pass


class BinanceRestrictedError(Exception):
    pass


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"OI Funding Scanner is running"
        )

    def do_HEAD(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server listening on port {port}",
        flush=True
    )

    server.serve_forever()


# ============================================================
# BINANCE GET
# ============================================================

def binance_get(endpoint, params=None, allow_failover=True):

    global CURRENT_BASE_URL

    last_error = None

    urls_to_try = []

    # Start with current working endpoint.
    urls_to_try.append(
        CURRENT_BASE_URL
    )

    # Add other endpoints as failover.
    if allow_failover:

        for base in BASE_URLS:

            if base not in urls_to_try:

                urls_to_try.append(base)

    for base_url in urls_to_try:

        url = base_url + endpoint

        for attempt in range(
            1,
            MAX_RETRIES + 1
        ):

            try:

                response = SESSION.get(
                    url,
                    params=params,
                    timeout=REQUEST_TIMEOUT
                )

                status = response.status_code

                # ------------------------------------------------
                # RATE LIMIT / IP BAN
                # ------------------------------------------------

                if status == 418:

                    raise BinanceRateLimitError(
                        "HTTP 418 - Binance IP auto-ban/rate limit"
                    )

                if status == 429:

                    raise BinanceRateLimitError(
                        "HTTP 429 - Binance rate limit"
                    )

                # ------------------------------------------------
                # GEO / RESTRICTED
                # ------------------------------------------------

                if status == 451:

                    raise BinanceRestrictedError(
                        "HTTP 451 - Binance server/location restricted"
                    )

                response.raise_for_status()

                data = response.json()

                # Current endpoint worked.
                CURRENT_BASE_URL = base_url

                return data

            except BinanceRateLimitError:

                raise

            except BinanceRestrictedError:

                raise

            except requests.RequestException as e:

                last_error = e

                print(
                    f"Binance request failed "
                    f"(attempt {attempt}/{MAX_RETRIES}) "
                    f"{base_url}: {e}",
                    flush=True
                )

                if attempt < MAX_RETRIES:

                    time.sleep(
                        2 * attempt
                    )

    if last_error:

        print(
            f"All Binance endpoints failed: {last_error}",
            flush=True
        )

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram token is missing.",
            flush=True
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID is missing.",
            flush=True
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:

        response = SESSION.post(
            url,
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:

            print(
                f"Telegram sent: {message}",
                flush=True
            )

            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text,
            flush=True
        )

        return False

    except requests.RequestException as e:

        print(
            "Telegram connection error:",
            e,
            flush=True
        )

        return False


# ============================================================
# LOAD STATE
# ============================================================

def load_state():

    if not os.path.exists(
        STATE_FILE
    ):

        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

            if isinstance(data, dict):

                return data

            return {}

    except Exception as e:

        print(
            "State load error:",
            e,
            flush=True
        )

        return {}


# ============================================================
# SAVE STATE
# ============================================================

def save_state(state):

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2
            )

    except Exception as e:

        print(
            "State save error:",
            e,
            flush=True
        )


# ============================================================
# GET SYMBOLS
# ============================================================

def get_symbols():

    global SYMBOL_CACHE
    global SYMBOL_CACHE_TIME

    now = time.time()

    # Use cache.
    if (
        SYMBOL_CACHE
        and
        now - SYMBOL_CACHE_TIME
        < SYMBOL_CACHE_SECONDS
    ):

        return SYMBOL_CACHE

    data = binance_get(
        "/fapi/v1/exchangeInfo"
    )

    if not data:

        return []

    symbols = []

    for item in data.get(
        "symbols",
        []
    ):

        if (
            item.get("quoteAsset") == "USDT"
            and
            item.get("contractType") == "PERPETUAL"
            and
            item.get("status") == "TRADING"
        ):

            symbol = item.get(
                "symbol"
            )

            if symbol:

                symbols.append(
                    symbol
                )

    if symbols:

        SYMBOL_CACHE = symbols

        SYMBOL_CACHE_TIME = now

    return symbols


# ============================================================
# GET ALL FUNDING RATES
# ============================================================

def get_all_funding():

    global FUNDING_CACHE
    global FUNDING_CACHE_TIME

    now = time.time()

    # Use short cache.
    if (
        FUNDING_CACHE
        and
        now - FUNDING_CACHE_TIME
        < FUNDING_CACHE_SECONDS
    ):

        return FUNDING_CACHE

    data = binance_get(
        "/fapi/v1/premiumIndex"
    )

    if not data:

        return {}

    funding = {}

    for item in data:

        symbol = item.get(
            "symbol"
        )

        rate = item.get(
            "lastFundingRate"
        )

        if not symbol:

            continue

        try:

            funding[symbol] = float(
                rate
            )

        except (
            TypeError,
            ValueError
        ):

            continue

    if funding:

        FUNDING_CACHE = funding

        FUNDING_CACHE_TIME = now

    return funding


# ============================================================
# FUNDING DIRECTION
# ============================================================

def get_funding_direction(
    symbol,
    funding_map
):

    funding = funding_map.get(
        symbol
    )

    if funding is None:

        return None

    if funding > 0:

        return "LONG"

    if funding < 0:

        return "SHORT"

    return None


# ============================================================
# GET OI HISTORY
# ============================================================

def get_oi_history(
    symbol,
    period
):

    data = binance_get(
        "/futures/data/openInterestHist",
        {
            "symbol": symbol,
            "period": period,
            "limit": 2
        }
    )

    if not data or len(data) < 2:

        return None

    try:

        old_oi = float(
            data[-2]["sumOpenInterest"]
        )

        new_oi = float(
            data[-1]["sumOpenInterest"]
        )

        return (
            old_oi,
            new_oi
        )

    except (
        KeyError,
        TypeError,
        ValueError
    ):

        return None


# ============================================================
# OI DIRECTION
# ============================================================

def get_oi_direction(
    symbol,
    period
):

    result = get_oi_history(
        symbol,
        period
    )

    if not result:

        return None

    old_oi, new_oi = result

    if old_oi <= 0:

        return None

    change_percent = (
        (new_oi - old_oi)
        / old_oi
    ) * 100

    if (
        change_percent
        > MIN_OI_CHANGE_PERCENT
    ):

        return "LONG"

    if (
        change_percent
        < -MIN_OI_CHANGE_PERCENT
    ):

        return "SHORT"

    return None


# ============================================================
# TIMEFRAME DIRECTION
# ============================================================

def get_timeframe_direction(
    symbol,
    period,
    funding_map
):

    oi_direction = get_oi_direction(
        symbol,
        period
    )

    if not oi_direction:

        return None

    funding_direction = (
        get_funding_direction(
            symbol,
            funding_map
        )
    )

    if not funding_direction:

        return None

    # OI and Funding must agree.
    if (
        oi_direction
        != funding_direction
    ):

        return None

    return oi_direction


# ============================================================
# CURRENT 5M CANDLE
# ============================================================

def get_current_5m_candle(
    symbol
):

    data = binance_get(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "5m",
            "limit": 1
        }
    )

    if not data:

        return None

    try:

        candle = data[0]

        return {
            "open_time": int(
                candle[0]
            ),

            "open": float(
                candle[1]
            ),

            "close": float(
                candle[4]
            )
        }

    except (
        TypeError,
        ValueError,
        IndexError
    ):

        return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def check_5m_direction(
    symbol,
    expected_direction
):

    candle = get_current_5m_candle(
        symbol
    )

    if not candle:

        return False

    now_ms = int(
        time.time() * 1000
    )

    seconds_from_open = (
        now_ms
        - candle["open_time"]
    ) / 1000

    # Wait after new candle starts.
    if (
        seconds_from_open
        < FIVE_MIN_CONFIRM_SECONDS
    ):

        return False

    open_price = candle[
        "open"
    ]

    current_price = candle[
        "close"
    ]

    if open_price <= 0:

        return False

    if (
        expected_direction == "LONG"
        and
        current_price > open_price
    ):

        return True

    if (
        expected_direction == "SHORT"
        and
        current_price < open_price
    ):

        return True

    return False


# ============================================================
# SETUP KEY
# ============================================================

def make_setup_key(
    symbol,
    direction
):

    candle = get_current_5m_candle(
        symbol
    )

    if not candle:

        return None

    return (
        f"{symbol}_"
        f"{direction}_"
        f"{candle['open_time']}"
    )


# ============================================================
# CHECK COIN
# ============================================================

def check_coin(
    symbol,
    state,
    funding_map
):

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    direction_1h = (
        get_timeframe_direction(
            symbol,
            "1h",
            funding_map
        )
    )

    if not direction_1h:

        return

    print(
        f"{symbol} 1H = {direction_1h}",
        flush=True
    )

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    direction_15m = (
        get_timeframe_direction(
            symbol,
            "15m",
            funding_map
        )
    )

    if not direction_15m:

        return

    print(
        f"{symbol} 15M = {direction_15m}",
        flush=True
    )

    # --------------------------------------------------------
    # 1H + 15M MUST MATCH
    # --------------------------------------------------------

    if (
        direction_1h
        != direction_15m
    ):

        return

    final_direction = direction_1h

    # --------------------------------------------------------
    # 5M CONFIRMATION
    # --------------------------------------------------------

    if not check_5m_direction(
        symbol,
        final_direction
    ):

        return

    # --------------------------------------------------------
    # SETUP KEY
    # --------------------------------------------------------

    setup_key = make_setup_key(
        symbol,
        final_direction
    )

    if not setup_key:

        return

    # --------------------------------------------------------
    # DUPLICATE CHECK
    # --------------------------------------------------------

    if (
        state.get(symbol)
        == setup_key
    ):

        return

    # --------------------------------------------------------
    # ALERT
    # --------------------------------------------------------

    message = (
        f"{symbol} "
        f"{final_direction}"
    )

    if send_telegram(
        message
    ):

        state[symbol] = setup_key

        save_state(
            state
        )

        print(
            f"ALERT SENT: {message}",
            flush=True
        )


# ============================================================
# SCAN ONCE
# ============================================================

def run_scan(state):

    print(
        "\n======================================",
        flush=True
    )

    print(
        "NEW SCAN:",
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        flush=True
    )

    print(
        "======================================",
        flush=True
    )

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    symbols = get_symbols()

    if not symbols:

        print(
            "No symbols received.",
            flush=True
        )

        return False

    print(
        f"USDT PERPETUALS: {len(symbols)}",
        flush=True
    )

    # --------------------------------------------------------
    # FUNDING
    # --------------------------------------------------------

    funding_map = get_all_funding()

    if not funding_map:

        print(
            "Funding data unavailable.",
            flush=True
        )

        return False

    print(
        f"Funding symbols: {len(funding_map)}",
        flush=True
    )

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    scan_start = time.time()

    checked = 0

    for symbol in symbols:

        try:

            check_coin(
                symbol,
                state,
                funding_map
            )

            checked += 1

        except BinanceRateLimitError:

            print(
                "\nBINANCE RATE LIMIT / IP BAN",
                flush=True
            )

            print(
                f"Cooling down for "
                f"{RATE_LIMIT_COOLDOWN} seconds...",
                flush=True
            )

            time.sleep(
                RATE_LIMIT_COOLDOWN
            )

            return False

        except BinanceRestrictedError as e:

            print(
                "BINANCE RESTRICTED:",
                str(e),
                flush=True
            )

            return False

        except Exception as e:

            print(
                f"{symbol} ERROR: {e}",
                flush=True
            )

        # Controlled request rate.
        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    elapsed = (
        time.time()
        - scan_start
    )

    print(
        f"Scan completed: "
        f"{checked}/{len(symbols)} coins "
        f"in {elapsed:.1f}s",
        flush=True
    )

    return True


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():

    state = load_state()

    print(
        "Scanner loop started.",
        flush=True
    )

    while True:

        cycle_start = time.time()

        try:

            run_scan(
                state
            )

        except BinanceRateLimitError as e:

            print(
                "Binance rate limit:",
                e,
                flush=True
            )

            time.sleep(
                RATE_LIMIT_COOLDOWN
            )

        except BinanceRestrictedError as e:

            print(
                "Binance restricted:",
                e,
                flush=True
            )

            time.sleep(
                RATE_LIMIT_COOLDOWN
            )

        except Exception as e:

            print(
                "SCANNER ERROR:",
                e,
                flush=True
            )

            time.sleep(
                30
            )

        elapsed = (
            time.time()
            - cycle_start
        )

        wait_time = max(
            1,
            SCAN_INTERVAL_SECONDS
            - elapsed
        )

        print(
            f"Next full scan in "
            f"{wait_time:.1f}s",
            flush=True
        )

        time.sleep(
            wait_time
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n======================================",
        flush=True
    )

    print(
        "BINANCE FUTURES OI + FUNDING SCANNER",
        flush=True
    )

    print(
        "1H OI + FUNDING",
        flush=True
    )

    print(
        "15M OI + FUNDING",
        flush=True
    )

    print(
        "5M PRICE CONFIRMATION",
        flush=True
    )

    print(
        "======================================",
        flush=True
    )

    print(
        "Starting...",
        flush=True
    )

    # --------------------------------------------------------
    # RENDER HEALTH SERVER
    # --------------------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # --------------------------------------------------------
    # TELEGRAM STARTUP
    # --------------------------------------------------------

    if send_telegram(
        "OI Funding Scanner Started"
    ):

        print(
            "Telegram startup notification sent.",
            flush=True
        )

    else:

        print(
            "Telegram startup notification FAILED.",
            flush=True
        )

    # --------------------------------------------------------
    # START SCANNER
    # --------------------------------------------------------

    scanner_loop()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
```
