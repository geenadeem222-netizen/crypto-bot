
import requests
import time
import json
import os
from datetime import datetime, timezone

# ============================================================
# BINANCE FUTURES OI + FUNDING + 1H/15M + 5M SCANNER
# ============================================================
#
# CONDITIONS:
#
# 1H:
#   OI UP   + Funding positive  = LONG
#   OI DOWN + Funding negative  = SHORT
#
# 15M:
#   OI UP   + Funding positive  = LONG
#   OI DOWN + Funding negative  = SHORT
#
# 1H and 15M direction MUST be the same.
#
# 5M:
#   When a NEW 5-minute candle starts,
#   price must move in the same direction.
#
# ALERT:
#   COIN LONG
#   COIN SHORT
#
# One alert per setup.
# The same setup will NOT repeatedly alert.
#
# ============================================================


# ============================================================
# TELEGRAM SETTINGS
# ============================================================

TELEGRAM_BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "PASTE_YOUR_CHAT_ID_HERE"


# ============================================================
# BINANCE
# ============================================================

BASE_URL = "https://fapi.binance.com"

SESSION = requests.Session()

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


# ============================================================
# SETTINGS
# ============================================================

SCAN_SECONDS = 30

# How many seconds after a new 5M candle begins
# before checking its first direction.
FIVE_MIN_CONFIRM_SECONDS = 15

# Minimum OI change required.
# 0 means any positive/negative change counts.
MIN_OI_CHANGE_PERCENT = 0.0

# State file.
# Used to prevent duplicate alerts.
STATE_FILE = "alert_state.json"


# ============================================================
# HTTP GET
# ============================================================

def binance_get(endpoint, params=None):

    try:

        url = BASE_URL + endpoint

        response = SESSION.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(f"Binance request error: {e}")

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or TELEGRAM_BOT_TOKEN.startswith("PASTE_")
    ):
        print("Telegram token is not configured.")
        return False

    if (
        not TELEGRAM_CHAT_ID
        or TELEGRAM_CHAT_ID.startswith("PASTE_")
    ):
        print("Telegram chat ID is not configured.")
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
            timeout=15
        )

        if response.ok:

            print("Telegram:", message)

            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text
        )

        return False

    except Exception as e:

        print("Telegram connection error:", e)

        return False


# ============================================================
# STARTUP MESSAGE
# ============================================================

def send_startup_message():

    send_telegram(
        "OI Funding Scanner Started Successfully"
    )


# ============================================================
# LOAD STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):

        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

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

        print("State save error:", e)


# ============================================================
# GET ALL USDT PERPETUAL SYMBOLS
# ============================================================

def get_symbols():

    data = binance_get(
        "/fapi/v1/exchangeInfo"
    )

    if not data:

        return []

    symbols = []

    for item in data.get("symbols", []):

        symbol = item.get("symbol")

        contract_type = item.get("contractType")

        quote_asset = item.get("quoteAsset")

        status = item.get("status")

        if (
            quote_asset == "USDT"
            and contract_type == "PERPETUAL"
            and status == "TRADING"
        ):

            symbols.append(symbol)

    return symbols


# ============================================================
# GET FUNDING RATE
# ============================================================

def get_funding(symbol):

    data = binance_get(
        "/fapi/v1/premiumIndex",
        {
            "symbol": symbol
        }
    )

    if not data:

        return None

    try:

        return float(
            data["lastFundingRate"]
        )

    except Exception:

        return None


# ============================================================
# GET OPEN INTEREST HISTORY
# ============================================================

def get_oi_history(symbol, period, limit=2):

    data = binance_get(
        "/futures/data/openInterestHist",
        {
            "symbol": symbol,
            "period": period,
            "limit": limit
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

        return old_oi, new_oi

    except Exception:

        return None


# ============================================================
# OI DIRECTION
# ============================================================

def get_oi_direction(symbol, period):

    result = get_oi_history(
        symbol,
        period,
        2
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
        f"{symbol} {period} OI change: "
        f"{change_percent:.4f}%"
    )

    if (
        change_percent > MIN_OI_CHANGE_PERCENT
    ):

        return "LONG"

    if (
        change_percent < -MIN_OI_CHANGE_PERCENT
    ):

        return "SHORT"

    return None


# ============================================================
# FUNDING DIRECTION
# ============================================================

def get_funding_direction(symbol):

    funding = get_funding(symbol)

    if funding is None:

        return None

    print(
        f"{symbol} Funding: "
        f"{funding:.8f}"
    )

    if funding > 0:

        return "LONG"

    if funding < 0:

        return "SHORT"

    return None


# ============================================================
# GET 1H DIRECTION
# ============================================================

def get_1h_direction(symbol):

    oi_direction = get_oi_direction(
        symbol,
        "1h"
    )

    funding_direction = get_funding_direction(
        symbol
    )

    if not oi_direction:
        return None

    if not funding_direction:
        return None

    # BOTH MUST BE SAME
    if (
        oi_direction
        == funding_direction
    ):

        return oi_direction

    return None


# ============================================================
# GET 15M DIRECTION
# ============================================================

def get_15m_direction(symbol):

    oi_direction = get_oi_direction(
        symbol,
        "15m"
    )

    funding_direction = get_funding_direction(
        symbol
    )

    if not oi_direction:
        return None

    if not funding_direction:
        return None

    # BOTH MUST BE SAME
    if (
        oi_direction
        == funding_direction
    ):

        return oi_direction

    return None


# ============================================================
# GET CURRENT 5M CANDLE
# ============================================================

def get_current_5m_candle(symbol):

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
            "open_time": int(candle[0]),
            "open": float(candle[1]),
            "close": float(candle[4])
        }

    except Exception:

        return None


# ============================================================
# CHECK 5M DIRECTION
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

    # Make sure we are actually
    # inside a new 5M candle.
    if (
        seconds_from_open
        < FIVE_MIN_CONFIRM_SECONDS
    ):

        return False

    open_price = candle["open"]
    current_price = candle["close"]

    if open_price <= 0:

        return False

    change_percent = (
        (current_price - open_price)
        / open_price
    ) * 100

    print(
        f"{symbol} 5M move: "
        f"{change_percent:.4f}%"
    )

    if (
        expected_direction == "LONG"
        and current_price > open_price
    ):

        return True

    if (
        expected_direction == "SHORT"
        and current_price < open_price
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

    # Current 5M candle start time
    candle = get_current_5m_candle(
        symbol
    )

    if not candle:

        return None

    candle_time = candle["open_time"]

    return (
        f"{symbol}_"
        f"{direction}_"
        f"{candle_time}"
    )


# ============================================================
# CHECK COIN
# ============================================================

def check_coin(
    symbol,
    state
):

    print(
        f"\nChecking {symbol}"
    )

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    direction_1h = get_1h_direction(
        symbol
    )

    if not direction_1h:

        return

    print(
        f"{symbol} 1H = "
        f"{direction_1h}"
    )

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    direction_15m = get_15m_direction(
        symbol
    )

    if not direction_15m:

        return

    print(
        f"{symbol} 15M = "
        f"{direction_15m}"
    )

    # --------------------------------------------------------
    # 1H AND 15M MUST MATCH
    # --------------------------------------------------------

    if (
        direction_1h
        != direction_15m
    ):

        print(
            f"{symbol}: "
            f"1H/15M direction mismatch"
        )

        return

    final_direction = direction_1h

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    if not check_5m_direction(
        symbol,
        final_direction
    ):

        return

    # --------------------------------------------------------
    # ONE ALERT PER SETUP
    # --------------------------------------------------------

    setup_key = make_setup_key(
        symbol,
        final_direction
    )

    if not setup_key:

        return

    if state.get(symbol) == setup_key:

        print(
            f"{symbol}: "
            f"Already alerted"
        )

        return

    # --------------------------------------------------------
    # TELEGRAM ALERT
    # --------------------------------------------------------

    message = (
        f"{symbol} "
        f"{final_direction}"
    )

    sent = send_telegram(
        message
    )

    if sent:

        state[symbol] = setup_key

        save_state(state)

        print(
            f"ALERT SENT: {message}"
        )


# ============================================================
# MAIN SCANNER
# ============================================================

def main():

    print(
        "\n======================================"
    )

    print(
        "BINANCE FUTURES OI + FUNDING SCANNER"
    )

    print(
        "======================================\n"
    )

    # --------------------------------------------------------
    # STARTUP TELEGRAM
    # --------------------------------------------------------

    send_startup_message()

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    symbols = get_symbols()

    if not symbols:

        print(
            "No Binance Futures symbols found."
        )

        return

    print(
        f"TOTAL USDT PERPETUAL SYMBOLS: "
        f"{len(symbols)}"
    )

    # --------------------------------------------------------
    # LOAD ALERT STATE
    # --------------------------------------------------------

    state = load_state()

    # --------------------------------------------------------
    # CONTINUOUS SCANNER
    # --------------------------------------------------------

    while True:

        scan_start = time.time()

        print(
            "\n--------------------------------------"
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
            "--------------------------------------"
        )

        # ----------------------------------------------------
        # CHECK EVERY USDT PERPETUAL
        # ----------------------------------------------------

        for symbol in symbols:

            try:

                check_coin(
                    symbol,
                    state
                )

            except Exception as e:

                print(
                    f"{symbol} ERROR: {e}"
                )

            # Small delay to reduce
            # Binance request pressure.
            time.sleep(0.10)

        # ----------------------------------------------------
        # WAIT
        # ----------------------------------------------------

        elapsed = (
            time.time()
            - scan_start
        )

        wait_time = max(
            1,
            SCAN_SECONDS - elapsed
        )

        print(
            f"\nScan completed in "
            f"{elapsed:.1f} seconds."
        )

        print(
            f"Next scan in "
            f"{wait_time:.1f} seconds."
        )

        time.sleep(
            wait_time
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
```
