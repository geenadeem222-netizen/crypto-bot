
import requests
import json

BASE = "https://api-dev.pipai.org"

tests = [
    (
        "PIPAI 5M KLINES",
        "/api/v1/klines",
        {"symbol": "BTC", "interval": 5}
    ),
    (
        "PIPAI 5M KLINES ALT",
        "/api/v1/klines",
        {"symbol": "BTCUSDT", "interval": 5}
    ),
    (
        "PIPAI 5M KLINES INTERVAL",
        "/api/v1/klines",
        {"symbol": "BTC", "interval": "5"}
    ),
]

for name, endpoint, params in tests:
    print("\n" + "=" * 70)
    print(name)
    print("=" * 70)

    url = BASE + endpoint

    try:
        r = requests.get(url, params=params, timeout=20)

        print("URL:", r.url)
        print("STATUS:", r.status_code)

        try:
            data = r.json()
            print("RESPONSE:")
            print(json.dumps(data, indent=2))
        except Exception:
            print("RESPONSE:")
            print(r.text[:3000])

    except Exception as e:
        print("ERROR:", repr(e))

print("\n" + "=" * 70)
print("5M TEST FINISHED")
print("=" * 70)
