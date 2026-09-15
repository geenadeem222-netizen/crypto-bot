import requests

print("====================================")
print("COINBEACON OPEN INTEREST TEST")
print("====================================")

url_15m = "https://api.coinbeacon.io/public/open-interest/BTCUSDT/history?period=15m"
url_1h = "https://api.coinbeacon.io/public/open-interest/BTCUSDT/history?period=1h"

try:
    print("\nTesting 15M Open Interest...")

    r15 = requests.get(url_15m, timeout=20)

    print("15M STATUS:", r15.status_code)

    if r15.ok:
        data15 = r15.json()

        print("15M DATA RECEIVED: YES")
        print("15M SAMPLES:", len(data15.get("data", data15)))

        print("\n15M RESPONSE:")
        print(data15)

    else:
        print("15M DATA RECEIVED: NO")
        print(r15.text[:1000])

except Exception as e:
    print("15M ERROR:", repr(e))


try:
    print("\nTesting 1H Open Interest...")

    r1h = requests.get(url_1h, timeout=20)

    print("1H STATUS:", r1h.status_code)

    if r1h.ok:
        data1h = r1h.json()

        print("1H DATA RECEIVED: YES")
        print("1H SAMPLES:", len(data1h.get("data", data1h)))

        print("\n1H RESPONSE:")
        print(data1h)

    else:
        print("1H DATA RECEIVED: NO")
        print(r1h.text[:1000])

except Exception as e:
    print("1H ERROR:", repr(e))


print("\n====================================")
print("COINBEACON TEST FINISHED")
print("====================================")
