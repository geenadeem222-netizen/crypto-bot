import requests
import json
import time

BASE_URL = "https://api.primit.xyz"

print("=" * 60)
print("PRIMIT OI + FUNDING API TEST")
print("=" * 60)


def test_endpoint(name, url, params=None):
    print(f"\nTesting {name}...")
    print("URL:", url)
    if params:
        print("PARAMS:", params)

    try:
        r = requests.get(
            url,
            params=params,
            timeout=20
        )

        print("STATUS:", r.status_code)

        try:
            data = r.json()
            print("DATA RECEIVED:", "YES" if data else "NO")
            print(json.dumps(data, indent=2)[:12000])
        except Exception:
            print("RAW RESPONSE:")
            print(r.text[:5000])

    except Exception as e:
        print("ERROR:", repr(e))


# ============================================================
# 1. EXCHANGE INFO
# ============================================================

test_endpoint(
    "EXCHANGE INFO",
    f"{BASE_URL}/fapi/v1/exchangeInfo"
)


# ============================================================
# 2. BTC 15M HISTORICAL OPEN INTEREST
# ============================================================

test_endpoint(
    "BTC 15M OPEN INTEREST",
    f"{BASE_URL}/futures/data/openInterestHist",
    {
        "symbol": "BTCUSDT",
        "period": "15m",
        "limit": 5
    }
)


# ============================================================
# 3. BTC 1H HISTORICAL OPEN INTEREST
# ============================================================

test_endpoint(
    "BTC 1H OPEN INTEREST",
    f"{BASE_URL}/futures/data/openInterestHist",
    {
        "symbol": "BTCUSDT",
        "period": "1h",
        "limit": 5
    }
)


# ============================================================
# 4. BTC FUNDING HISTORY
# ============================================================

test_endpoint(
    "BTC FUNDING HISTORY",
    f"{BASE_URL}/fapi/v1/fundingRate",
    {
        "symbol": "BTCUSDT",
        "limit": 5
    }
)


# ============================================================
# 5. BTC CURRENT OPEN INTEREST
# ============================================================

test_endpoint(
    "BTC CURRENT OPEN INTEREST",
    f"{BASE_URL}/fapi/v1/openInterest",
    {
        "symbol": "BTCUSDT"
    }
)


# ============================================================
# 6. BTC 5M PRICE CANDLES
# ============================================================

test_endpoint(
    "BTC 5M CANDLES",
    f"{BASE_URL}/fapi/v1/klines",
    {
        "symbol": "BTCUSDT",
        "interval": "5m",
        "limit": 5
    }
)


# ============================================================
# 7. BTC 15M PRICE CANDLES
# ============================================================

test_endpoint(
    "BTC 15M CANDLES",
    f"{BASE_URL}/fapi/v1/klines",
    {
        "symbol": "BTCUSDT",
        "interval": "15m",
        "limit": 5
    }
)


# ============================================================
# 8. BTC 1H PRICE CANDLES
# ============================================================

test_endpoint(
    "BTC 1H CANDLES",
    f"{BASE_URL}/fapi/v1/klines",
    {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "limit": 5
    }
)


print("\n" + "=" * 60)
print("PRIMIT TEST FINISHED")
print("=" * 60)
