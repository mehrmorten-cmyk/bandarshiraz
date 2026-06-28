import os
import json
import time
import psycopg2
import logging
import hashlib
import threading
import requests
import re
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
        "keywords": ["استان فارس", "شیراز", "مرودشت", "جهرم", "کازرون", "فسا"]
    },
    "hormozgan": {
        "name": "هرمزگان",
        "channel": os.environ.get("CHANNEL_ID_HORMOZGAN"),
        "keywords": ["استان هرمزگان", "بندرعباس", "قشم", "کیش", "میناب", "بندرلنگه"]
    }
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"DB Init Error: {e}")

def is_seen(link_hash):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (link_hash,))
        exists = cur.fetchone()
        cur.close()
        conn.close()
        return exists is not None
    except: return False

def mark_seen(link_hash):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO seen_news (hash) VALUES (%s) ON CONFLICT DO NOTHING", (link_hash,))
        conn.commit()
        cur.close()
        conn.close()
    except: pass

def gemini_call(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        text = resp.json()['candidates'][0]['content']['parts'][0]['text']
        return text
    except: return None

def run_check():
    init_db()
    for p_id, config in PROVINCES.items():
        if not config['channel']: continue
        logging.info(f"Searching for {config['name']}...")
        
        for kw in config['keywords']:
            try:
                rss_url = f"https://news.google.com/rss/search?q={quote(kw)}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
                root = ElementTree.fromstring(requests.get(rss_url).content)
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
            except: continue

@app.route('/')
def home(): return "Bot is Online"

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Process Started"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))