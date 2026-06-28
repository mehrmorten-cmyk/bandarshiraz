import os
import requests
from flask import Flask

# تنظیمات پایه
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_FARS = "-1004352884396"
CHANNEL_HORMOZGAN = "-1003915149928"

app = Flask(__name__)

def send_test_message(chat_id, province_name):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    text = f"✅ اتصال برقرار است!\n📍 این یک پیام تست برای استان {province_name} است.\nسیستم آماده دریافت اخبار می‌باشد."
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload)
        return r.json()
    except Exception as e:
        return str(e)

@app.route('/')
def home():
    return "Bot is Online"

@app.route('/check')
def check():
    # ارسال مستقیم به هر دو کانال برای تست اتصال
    res_fars = send_test_message(CHANNEL_FARS, "فارس")
    res_hormozgan = send_test_message(CHANNEL_HORMOZGAN, "هرمزگان")
    
    return {
        "status": "Test messages sent",
        "fars_response": res_fars,
        "hormozgan_response": res_hormozgan
    }

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
