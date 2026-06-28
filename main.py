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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PROVINCES = {
    "fars": {
        "name": "فارس",
        "channel": os.environ.get("CHANNEL_ID_FARS"),
        "keywords": ["استان فارس", "شیراز", "مرودشت", "جهرم", "کازرون"]
    },
    "hormozgan": {
        "name": "هرمزگان",
        "channel": os.environ.get("CHANNEL_ID_HORMOZGAN"),
        "keywords": ["هرمزگان", "بندرعباس", "قشم", "کیش", "میناب"]
    }
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def init_db():
    """ساخت جدول دیتابیس در همان ابتدای کار"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database connection successful and table ensured.")
    except Exception as e:
        print(f"❌ DATABASE ERROR: {e}")

def is_seen(link_hash):
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (link_hash,))
        exists = cur.fetchone()
        cur.close()
        conn.close()
        return exists is not None
    except: return False

def mark_seen(link_hash):
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("INSERT INTO seen_news (hash) VALUES (%s) ON CONFLICT DO NOTHING", (link_hash,))
        conn.commit()
        cur.close()
        conn.close()
    except: pass

def run_check():
    print("🚀 Starting news search cycle...")
    for p_id, config in PROVINCES.items():
        if not config['channel']: continue
        
        for kw in config['keywords']:
            try:
                rss_url = f"https://news.google.com/rss/search?q={quote(kw)}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
                resp = requests.get(rss_url, timeout=15)
                root = ElementTree.fromstring(resp.content)
                items = root.findall(".//item")
                
                print(f"🔍 Searching {kw}... Found {len(items)} items.")
                
                for item in items[:5]:
                    link = item.findtext("link")
                    title = item.findtext("title")
                    h = hashlib.md5(link.encode()).hexdigest()
                    
                    if not is_seen(h):
                        print(f"📤 Sending new article: {title[:50]}...")
                        msg = f"📍 <b>خبر تازه: استان {config['name']}</b>\n\n🔹 {title}\n\n🔗 <a href='{link}'>مشاهده منبع خبر</a>"
                        tg_resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                      json={"chat_id": config['channel'], "text": msg, "parse_mode": "HTML"})
                        
                        if tg_resp.status_code == 200:
                            mark_seen(h)
                        else:
                            print(f"❌ Telegram Error: {tg_resp.text}")
                        time.sleep(2)
            except Exception as e:
                print(f"⚠️ Error searching {kw}: {e}")

@app.route('/')
def home():
    return f"Bot is Online. Last check: {datetime.now()}"

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "<h1>Check Started!</h1><p>The bot is now searching for news in the background. Check your Telegram channels.</p>"

# اجرای خودکار دیتابیس موقع بالا آمدن برنامه
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
