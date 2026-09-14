import requests
import time
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone


# ============================================================
# BINANCE FUTURES OI + FUNDING SCANNER
# ============================================================

BASE_URL = "https://fapi.binance.com"

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 OI-Funding-Scanner/1.0"
})


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ============================================================
# SETTINGS
# ============================================================

SCAN_SECONDS = 30

# Wait this many seconds after a new 5M candle starts
FIVE_MIN_CONFIRM_SECONDS = 15

# Minimum OI movement required
MIN_OI_CHANGE_PERCENT = 0.0

STATE_FILE = "alert_state.json"

REQUEST_TIMEOUT = 15

MAX_RETRIES = 3


# ============================================================
# BINANCE RESTRICTED ERROR
# ============================================================

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
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"OI Funding Scanner is running"
        )

    def do_HEAD(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

    def log_message(self, format, *args):

        return


def start_health_server():

    # Render automatically provides PORT
    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server listening on port {port}"
    )

    server.serve_forever()


# ============================================================
# BINANCE GET
# ============================================================

def binance_get(endpoint, params=None):

    url = BASE_URL + endpoint

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

            # Binance geo restriction
            if response.status_code == 451:

                raise BinanceRestrictedError(
                    "Binance API returned HTTP 451. "
                    "The server IP/location is restricted."
                )

            response.raise_for_status()

            return response.json()

        except BinanceRestrictedError:

            raise

        except requests.RequestException as e:

            print(
                f"Binance request failed "
                f"(attempt {attempt}/{MAX_RETRIES}): {e}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    2 * attempt
                )

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram token is missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID is missing."
        )

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
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:

            print(
                f"Telegram sent: {message}"
            )

            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text
        )

        return False

    except requests.RequestException as e:

        print(
            "Telegram connection error:",
            e
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

            return json.load(f)

    except Exception as e:

        print(
            "State load error:",
            e
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
            e
        )


# ============================================================
# GET SYMBOLS
# ============================================================

def get_symbols():

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

            symbols.append(
                item["symbol"]
            )

    return symbols


# ============================================================
# GET ALL FUNDING RATES
# ============================================================

def get_all_funding():

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

        try:

            funding[symbol] = float(
                rate
            )

        except (
            TypeError,
            ValueError
        ):

            continue

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

    print(
        f"{symbol} {period} OI: "
        f"{change_percent:+.4f}%"
    )

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

    # OI and funding must agree
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

    # Wait for confirmation
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

    change_percent = (
        (current_price - open_price)
        / open_price
    ) * 100

    print(
        f"{symbol} 5M: "
        f"{change_percent:+.4f}%"
    )

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

    print(
        f"\nChecking {symbol}"
    )

    # ========================================================
    # 1H
    # ========================================================

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
        f"{symbol} 1H = "
        f"{direction_1h}"
    )

    # ========================================================
    # 15M
    # ========================================================

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
        f"{symbol} 15M = "
        f"{direction_15m}"
    )

    # ========================================================
    # 1H + 15M MUST MATCH
    # ========================================================

    if (
        direction_1h
        != direction_15m
    ):

        print(
            f"{symbol}: "
            f"1H/15M mismatch"
        )

        return

    final_direction = direction_1h

    # ========================================================
    # 5M CONFIRMATION
    # ========================================================

    if not check_5m_direction(
        symbol,
        final_direction
    ):

        return

    # ========================================================
    # SETUP KEY
    # ========================================================

    setup_key = make_setup_key(
        symbol,
        final_direction
    )

    if not setup_key:

        return

    # ========================================================
    # DUPLICATE CHECK
    # ========================================================

    if (
        state.get(symbol)
        == setup_key
    ):

        print(
            f"{symbol}: "
            f"Already alerted"
        )

        return

    # ========================================================
    # ALERT
    # ========================================================

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
            f"ALERT SENT: "
            f"{message}"
        )


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():

    state = load_state()

    while True:

        try:

            print(
                "\n======================================"
            )

            print(
                "NEW SCAN:",
                datetime.now(
                    timezone.utc
                ).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )
            )

            print(
                "======================================"
            )

            # ------------------------------------------------
            # GET SYMBOLS
            # ------------------------------------------------

            symbols = get_symbols()

            if not symbols:

                print(
                    "No symbols received."
                )

                print(
                    "Retrying in 30 seconds..."
                )

                time.sleep(
                    30
                )

                continue

            print(
                f"USDT PERPETUALS: "
                f"{len(symbols)}"
            )

            # ------------------------------------------------
            # GET FUNDING ONCE
            # ------------------------------------------------

            funding_map = (
                get_all_funding()
            )

            if not funding_map:

                print(
                    "Funding data unavailable."
                )

                time.sleep(
                    30
                )

                continue

            # ------------------------------------------------
            # SCAN COINS
            # ------------------------------------------------

            scan_start = time.time()

            for symbol in symbols:

                try:

                    check_coin(
                        symbol,
                        state,
                        funding_map
                    )

                except BinanceRestrictedError:

                    raise

                except Exception as e:

                    print(
                        f"{symbol} ERROR: "
                        f"{e}"
                    )

                # Small delay to reduce API pressure
                time.sleep(
                    0.10
                )

            elapsed = (
                time.time()
                - scan_start
            )

            wait_time = max(
                1,
                SCAN_SECONDS - elapsed
            )

            print(
                f"\nScan completed: "
                f"{elapsed:.1f}s"
            )

            print(
                f"Next scan: "
                f"{wait_time:.1f}s"
            )

            time.sleep(
                wait_time
            )

        except BinanceRestrictedError as e:

            print(
                "\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
            )

            print(
                "BINANCE HTTP 451"
            )

            print(
                str(e)
            )

            print(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            )

            # Keep Render alive
            time.sleep(
                60
            )

        except Exception as e:

            print(
                "SCANNER ERROR:",
                e
            )

            time.sleep(
                30
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n======================================"
    )

    print(
        "BINANCE FUTURES OI + FUNDING SCANNER"
    )

    print(
        "======================================"
    )

    print(
        "Starting..."
    )

    # --------------------------------------------------------
    # START RENDER HEALTH SERVER
    # --------------------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # --------------------------------------------------------
    # TELEGRAM STARTUP NOTIFICATION
    # --------------------------------------------------------

    send_telegram(
        "OI Funding Scanner Started"
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
