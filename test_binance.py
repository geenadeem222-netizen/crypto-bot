import requests
import json

BASE_URL = "https://api-dev.pipai.org"

print("=" * 70)
print("PIPAI / PRIMIT FREE OI + FUNDING TEST")
print("=" * 70)


def test(name, endpoint, params=None):
    print("\n" + "-" * 70)
    print(name)
    print("-" * 70)

    url = BASE_URL + endpoint

    print("URL:", url)

    if params:
        print("PARAMS:", params)

    try:
        r = requests.get(
            url,
            params=params,
            timeout=25,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        print("STATUS:", r.status_code)

        print("\nRESPONSE:")

        try:
            data = r.json()
            print(json.dumps(data, indent=2)[:15000])
        except Exception:
            print(r.text[:10000])

    except Exception as e:
        print("ERROR:", repr(e))


# ============================================================
# 1. 15M HISTORICAL OPEN INTEREST
# ============================================================

test(
    "BTC 15M HISTORICAL OPEN INTEREST",
    "/openInterestHist",
    {
        "symbol": "BTCUSDT",
        "period": "15m",
        "limit": 5
    }
)


# ============================================================
# 2. 1H HISTORICAL OPEN INTEREST
# ============================================================

test(
    "BTC 1H HISTORICAL OPEN INTEREST",
    "/openInterestHist",
    {
        "symbol": "BTCUSDT",
        "period": "1h",
        "limit": 5
    }
)


# ============================================================
# 3. HISTORICAL FUNDING
# ============================================================

test(
    "BTC HISTORICAL FUNDING",
    "/funding/rates/BTCUSDT/history",
    {
        "limit": 5
    }
)


# ============================================================
# 4. CURRENT OPEN INTEREST
# ============================================================

test(
    "BTC CURRENT OPEN INTEREST",
    "/fapi/v1/openInterest",
    {
        "symbol": "BTCUSDT"
    }
)


# ============================================================
# 5. CURRENT FUNDING
# ============================================================

test(
    "BTC CURRENT FUNDING",
    "/funding/rates/BTCUSDT"
)


# ============================================================
# 6. 5M FUTURES CANDLES
# ============================================================

test(
    "BTC 5M FUTURES CANDLES",
    "/fapi/v1/klines",
    {
        "symbol": "BTCUSDT",
        "interval": "5m",
        "limit": 5
    }
)


# ============================================================
# 7. 15M FUTURES CANDLES
# ============================================================

test(
    "BTC 15M FUTURES CANDLES",
    "/fapi/v1/klines",
    {
        "symbol": "BTCUSDT",
        "interval": "15m",
        "limit": 5
    }
)


# ============================================================
# 8. 1H FUTURES CANDLES
# ============================================================

test(
    "BTC 1H FUTURES CANDLES",
    "/fapi/v1/klines",
    {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "limit": 5
    }
)


print("\n")
print("=" * 70)
print("PIPAI / PRIMIT TEST FINISHED")
print("=" * 70)
