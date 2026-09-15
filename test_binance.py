import requests
import json

BASE = "https://marginpad.io/api/v1"

print("====================================")
print("MARGINPAD FREE API TEST")
print("====================================")


def test_endpoint(name, endpoint):
    print(f"\nTesting {name}...")
    print(endpoint)

    try:
        response = requests.get(endpoint, timeout=20)

        print("STATUS:", response.status_code)

        if response.status_code == 200:
            data = response.json()

            print("DATA RECEIVED: YES")
            print(json.dumps(data, indent=2)[:5000])

        else:
            print("DATA RECEIVED: NO")
            print(response.text[:2000])

    except Exception as e:
        print("ERROR:", repr(e))


# Symbols
test_endpoint(
    "SYMBOLS",
    f"{BASE}/symbols"
)

# Funding
test_endpoint(
    "FUNDING",
    f"{BASE}/funding"
)

# Open Interest
test_endpoint(
    "OPEN INTEREST",
    f"{BASE}/open-interest"
)

# 5 minute candles
test_endpoint(
    "5M CANDLES",
    f"{BASE}/klines?symbol=BTC&interval=5"
)

# 15 minute candles
test_endpoint(
    "15M CANDLES",
    f"{BASE}/klines?symbol=BTC&interval=15"
)

# 1 hour candles
test_endpoint(
    "1H CANDLES",
    f"{BASE}/klines?symbol=BTC&interval=60"
)

print("\n====================================")
print("MARGINPAD TEST FINISHED")
print("====================================")
