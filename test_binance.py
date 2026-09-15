import requests

BASE = "https://api.bybit.com"

print("====================================")
print("BYBIT PUBLIC API TEST")
print("====================================")

symbol = "BTCUSDT"

# 15M Open Interest
try:
    url = f"{BASE}/v5/market/open-interest"
    params = {
        "category": "linear",
        "symbol": symbol,
        "intervalTime": "15min",
        "limit": 5
    }

    r = requests.get(url, params=params, timeout=20)

    print("\n15M OI STATUS:", r.status_code)

    if r.status_code == 200:
        data = r.json()

        if data.get("retCode") == 0:
            items = data.get("result", {}).get("list", [])
            print("15M OI DATA: SUCCESS")
            print("Samples:", len(items))
            print(items)
        else:
            print("15M OI API ERROR:")
            print(data)
    else:
        print(r.text[:1000])

except Exception as e:
    print("15M OI ERROR:", repr(e))


# 1H Open Interest
try:
    params = {
        "category": "linear",
        "symbol": symbol,
        "intervalTime": "1h",
        "limit": 5
    }

    r = requests.get(url, params=params, timeout=20)

    print("\n1H OI STATUS:", r.status_code)

    if r.status_code == 200:
        data = r.json()

        if data.get("retCode") == 0:
            items = data.get("result", {}).get("list", [])
            print("1H OI DATA: SUCCESS")
            print("Samples:", len(items))
            print(items)
        else:
            print("1H OI API ERROR:")
            print(data)
    else:
        print(r.text[:1000])

except Exception as e:
    print("1H OI ERROR:", repr(e))


# Funding
try:
    url_funding = f"{BASE}/v5/market/funding/history"

    params = {
        "category": "linear",
        "symbol": symbol,
        "limit": 5
    }

    r = requests.get(url_funding, params=params, timeout=20)

    print("\nFUNDING STATUS:", r.status_code)

    if r.status_code == 200:
        data = r.json()

        if data.get("retCode") == 0:
            items = data.get("result", {}).get("list", [])
            print("FUNDING DATA: SUCCESS")
            print("Samples:", len(items))
            print(items)
        else:
            print("FUNDING API ERROR:")
            print(data)
    else:
        print(r.text[:1000])

except Exception as e:
    print("FUNDING ERROR:", repr(e))


print("\n====================================")
print("BYBIT TEST FINISHED")
print("====================================")
