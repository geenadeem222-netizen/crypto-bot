import requests
import os

print("BINANCE CONNECTION TEST", flush=True)

urls = [
    "https://fapi.binance.com/fapi/v1/time",
    "https://fapi1.binance.com/fapi/v1/time",
    "https://fapi2.binance.com/fapi/v1/time",
    "https://fapi3.binance.com/fapi/v1/time",
]

for url in urls:
    try:
        print(f"\nTesting: {url}", flush=True)

        r = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=15
        )

        print(
            f"STATUS: {r.status_code}",
            flush=True
        )

        print(
            f"RESPONSE: {r.text[:500]}",
            flush=True
        )

    except Exception as e:
        print(
            f"ERROR: {e}",
            flush=True
        )

print("\nTEST FINISHED", flush=True)

