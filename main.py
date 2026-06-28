import os
import json
import time
import pg8000.native
import logging
import hashlib
import threading
import requests
from datetime import datetime
from urllib.parse import quote, urlparse
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

def get_db():
    # استخراج اطلاعات از DATABASE_URL برای pg8000
    p = urlparse(DATABASE_URL)
    return pg8000.native.Connection(
        user=p.username,
        password=p.password,
        host=p.hostname,
        port=p.port,
        database=p.path[1:],
        ssl_context=True
    )

def init_db():
    try:
        db = get_db()
        db.run("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        logging.info("Database Ready.")
    except Exception as e:
        logging.error(f"DB Init Error: {e}")

def is_seen(link_hash):
    try:
        db = get_db()
        res = db.run("SELECT 1 FROM seen_news WHERE hash = :h", h=link_hash)
        return len(res) > 0
    except: return False

def mark_seen(link_hash):
    try:
        db = get_db()
        db.run("INSERT INTO seen_news (hash) VALUES (:h) ON CONFLICT DO NOTHING", h=link_hash)
    except: pass

def run_check():
    init_db()
    for p_id, config in PROVINCES.items():
        if not config['channel']: continue
        logging.info(f"Checking {config['name']}...")
        
        for kw in config['keywords']:
            try:
                rss_url = f"https://news.google.com/rss/search?q={quote(kw)}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
                resp = requests.get(rss_url, timeout=10)
                root = ElementTree.fromstring(resp.content)
                for item in root.findall(".//item")[:5]:
                    link = item.findtext("link")
                    title = item.findtext("title")
                    h = hashlib.md5(link.encode()).hexdigest()
                    
                    if not is_seen(h):
                        msg = f"📍 <b>خبر تازه: استان {config['name']}</b>\n\n🔹 {title}\n\n🔗 <a href='{link}'>منبع خبر</a>"
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                      json={"chat_id": config['channel'], "text": msg, "parse_mode": "HTML"})
                        mark_seen(h)
                        time.sleep(1)
            except Exception as e:
                logging.error(f"Error in {kw}: {e}")

@app.route('/')
def home(): return "Bot is Online (v2.1)"

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check Started"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
