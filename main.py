import os
import requests
from flask import Flask

app = Flask(__name__)

# دریافت اطلاعات از رندر
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
FARS = "-1004352884396"
HORMOZGAN = "-1003915149928"

def send(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text})
    return r.json()

@app.route('/')
def home():
    return "Bot is Online"

@app.route('/check')
def check():
    # ارسال مستقیم و نمایش نتیجه در مرورگر (بدون ترد و پس‌زمینه)
    res1 = send(FARS, "🔴 تست اتصال: کانال استان فارس برقرار است.")
    res2 = send(HORMOZGAN, "🔵 تست اتصال: کانال استان هرمزگان برقرار است.")
    
    return {
        "fars_result": res1,
        "hormozgan_result": res2
    }

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
