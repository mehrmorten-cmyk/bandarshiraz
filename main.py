import os
import json
import time
import psycopg2
import logging
import hashlib
import threading
import requests
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree
from flask import Flask

# تنظیمات اصلی
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# لیست استان‌ها
PROVINCES = {
    "fars": {
        "name": "فارس",
        "channel": os.environ.get("CHANNEL_ID_FARS"),
        "keywords": ["شیراز", "استان فارس", "مرودشت"]
    },
    "hormozgan": {
        "name": "هرمزگان",
        "channel": os.environ.get("CHANNEL_ID_HORMOZGAN"),
        "keywords": ["بندرعباس", "هرمزگان", "قشم"]
    }
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def init_db():
    """ایجاد جدول دیتابیس در زمان استارت ربات"""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit()
        cur.close()
        print("✅ DATABASE STATUS: Connected and Table is Ready.")
    except Exception as e:
        print(f"❌ DATABASE ERROR: {e}")
    finally:
        if conn: conn.close()

def is_seen(h):
    conn = None
    exists = False
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
        exists = cur.fetchone() is not None
        cur.close()
    except: pass
    finally:
        if conn: conn.close()
    return exists

def mark_seen(h):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("INSERT INTO seen_news (hash) VALUES (%s) ON CONFLICT DO NOTHING", (h,))
        conn.commit()
        cur.close()
    except: pass
    finally:
        if conn: conn.close()

def run_check():
    print("🚀 News Scan Started...")
    for p_id, config in PROVINCES.items():
        if not config['channel']: continue
        for kw in config['keywords']:
            try:
                url = f"https://news.google.com/rss/search?q={quote(kw)}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
                resp = requests.get(url, timeout=15)
                root = ElementTree.fromstring(resp.content)
                for item in root.findall(".//item")[:5]:
                    link = item.findtext("link")
                    title = item.findtext("title")
                    h = hashlib.md5(link.encode()).hexdigest()
                    if not is_seen(h):
                        txt = f"📍 <b>خبر {config['name']}</b>\n\n🔹 {title}\n\n🔗 <a href='{link}'>منبع</a>"
                        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                          json={"chat_id": config['channel'], "text": txt, "parse_mode": "HTML"})
                        if r.status_code == 200: mark_seen(h)
                        time.sleep(2)
            except Exception as e:
                print(f"Error in {kw}: {e}")

@app.route('/')
def home(): return "Bot is Online"

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Started"

# اجرای خودکار زمان شروع
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
