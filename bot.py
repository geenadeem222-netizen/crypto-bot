import os
import time
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Yahan apne coins aur conditions badal sakte hain
ALERTS = [
    {"coin": "bitcoin", "vs_currency": "usd", "condition": "above", "target": 95000.0, "triggered": False},
    {"coin": "ethereum", "vs_currency": "usd", "condition": "below", "target": 2500.0, "triggered": False}
]

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error sending message: {e}")

def check_prices():
    coin_ids = ",".join(set(item["coin"] for item in ALERTS))
    vs_currencies = ",".join(set(item["vs_currency"] for item in ALERTS))
    
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_ids}&vs_currencies={vs_currencies}"
    
    try:
        response = requests.get(url)
        data = response.json()
        
        for alert in ALERTS:
            coin = alert["coin"]
            vs = alert["vs_currency"]
            target = alert["target"]
            condition = alert["condition"]
            
            if coin in data and vs in data[coin]:
                current_price = data[coin][vs]
                print(f"Checked {coin}: {current_price} {vs.upper()} (Target: {target})")
                
                if condition == "above" and current_price >= target and not alert["triggered"]:
                    send_telegram_message(f"🚨 *Alert Triggered!*\n{coin.upper()} is now **{current_price} {vs.upper()}** (Above target: {target})")
                    alert["triggered"] = True
                
                elif condition == "below" and current_price <= target and not alert["triggered"]:
                    send_telegram_message(f"🚨 *Alert Triggered!*\n{coin.upper()} is now **{current_price} {vs.upper()}** (Below target: {target})")
                    alert["triggered"] = True
                    
                elif condition == "above" and current_price < target:
                    alert["triggered"] = False
                elif condition == "below" and current_price > target:
                    alert["triggered"] = False
                    
    except Exception as e:
        print(f"API Error: {e}")

if __name__ == "__main__":
    print("Crypto Alert Bot Started...")
    send_telegram_message("🤖 Crypto Alert Bot is online and monitoring 24/7!")
    while True:
        check_prices()
        time.sleep(60)
