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

# تنظیمات لاگ برای مشاهده دقیق جزئیات در رندر
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# دریافت تنظیمات از محیط
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# پیکربندی استان‌ها
PROVINCES = {
    "fars": {
        "name": "فارس",
        "channel": os.environ.get("CHANNEL_ID_FARS"),
        "keywords": ["شیراز", "استان فارس", "مرودشت", "کازرون"]
    },
    "hormozgan": {
        "name": "هرمزگان",
        "channel": os.environ.get("CHANNEL_ID_HORMOZGAN"),
        "keywords": ["بندرعباس", "استان هرمزگان", "قشم", "کیش"]
    }
}

app = Flask(__name__)

def init_db():
    """ایجاد جداول دیتابیس در صورت عدم وجود"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database Init Error: {e}")

def is_seen(link_hash):
    """بررسی تکراری بودن خبر"""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (link_hash,))
        res = cur.fetchone()
        cur.close()
        return res is not None
    except Exception as e:
        logger.error(f"Database query error: {e}")
        return False
    finally:
        if conn: conn.close()

def mark_seen(link_hash):
    """ذخیره خبر در دیتابیس"""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("INSERT INTO seen_news (hash) VALUES (%s) ON CONFLICT DO NOTHING", (link_hash,))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error(f"Database insert error: {e}")
    finally:
        if conn: conn.close()

def run_check():
    """چرخه جستجو و ارسال خبر"""
    logger.info("🚀 News check cycle started...")
    for p_id, config in PROVINCES.items():
        if not config['channel']: continue
        
        for kw in config['keywords']:
            try:
                # جستجو در گوگل نیوز
                url = f"https://news.google.com/rss/search?q={quote(kw)}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
                resp = requests.get(url, timeout=15)
                root = ElementTree.fromstring(resp.content)
                
                for item in root.findall(".//item")[:5]:
                    link = item.findtext("link")
                    title = item.findtext("title")
                    link_hash = hashlib.md5(link.encode()).hexdigest()
                    
                    if not is_seen(link_hash):
                        logger.info(f"New article for {config['name']}: {title[:50]}")
                        msg = f"📍 <b>خبر تازه: استان {config['name']}</b>\n\n🔹 {title}\n\n🔗 <a href='{link}'>مشاهده منبع</a>"
                        
                        # ارسال به تلگرام
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        tg_resp = requests.post(tg_url, json={"chat_id": config['channel'], "text": msg, "parse_mode": "HTML"})
                        
                        if tg_resp.status_code == 200:
                            mark_seen(link_hash)
                        time.sleep(2) # وقفه برای جلوگیری از اسپم
            except Exception as e:
                logger.error(f"Error checking {kw}: {e}")

@app.route('/')
def home(): return "Bot status: Online"

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check started. Monitoring logs..."

# اجرای تنظیمات اولیه دیتابیس
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
