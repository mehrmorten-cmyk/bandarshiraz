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

# Configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Provinces & Sources
PROVINCES = {
    "fars": {
        "name": "فارس",
        "channel": os.environ.get("CHANNEL_ID_FARS"),
        "keywords": ["شیراز", "استان فارس", "مرودشت", "کازرون", "جهرم"],
        "rss_feeds": [
            "https://www.tasnimnews.com/fa/rss/service/0/8", # تسنیم فارس
            "https://www.irna.ir/rss/service/131"            # ایرنا فارس
        ]
    },
    "hormozgan": {
        "name": "هرمزگان",
        "channel": os.environ.get("CHANNEL_ID_HORMOZGAN"),
        "keywords": ["هرمزگان", "بندرعباس", "قشم", "کیش", "میناب"],
        "rss_feeds": [
            "https://www.tasnimnews.com/fa/rss/service/0/13", # تسنیم هرمزگان
            "https://www.irna.ir/rss/service/151"             # ایرنا هرمزگان
        ]
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
        print(f"DB Error: {e}")

def is_seen(h):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
        res = cur.fetchone()
        cur.close()
        conn.close()
        return res is not None
    except: return False

def mark_seen(h):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO seen_news (hash) VALUES (%s) ON CONFLICT DO NOTHING", (h,))
        conn.commit()
        cur.close()
        conn.close()
    except: pass

def gemini_rewrite(title, province):
    """بازنویسی خبر با پروتکل مقاومت توسط هوش مصنوعی"""
    if not GEMINI_API_KEY: return title
    prompt = f"خبر زیر را درباره استان {province} با لحن خبری و انقلابی (پروتکل مقاومت) بازنویسی کن. از کلمات رژیم، قیام و کانون‌های شورشی در صورت تناسب استفاده کن. فقط عنوان نهایی را بده:\n{title}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return title

def fetch_rss(url):
    try:
        resp = requests.get(url, timeout=15)
        root = ElementTree.fromstring(resp.content)
        return [{"title": i.findtext("title"), "link": i.findtext("link")} for i in root.findall(".//item")[:10]]
    except: return []

def search_google_x(keyword):
    """جستجوی پست‌های ایکس/توییتر از طریق گوگل"""
    try:
        q = quote(f"{keyword} site:x.com OR site:twitter.com")
        url = f"https://news.google.com/rss/search?q={q}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
        resp = requests.get(url, timeout=15)
        root = ElementTree.fromstring(resp.content)
        return [{"title": i.findtext("title"), "link": i.findtext("link")} for i in root.findall(".//item")[:5]]
    except: return []

def run_check():
    print("🚀 Advanced Scraper Started...")
    for p_id, config in PROVINCES.items():
        if not config['channel']: continue
        
        all_news = []
        # ۱. چک کردن RSS مستقیم خبرگزاری‌ها
        for feed in config['rss_feeds']:
            all_news.extend(fetch_rss(feed))
        
        # ۲. چک کردن ایکس (توییتر) و اخبار عمومی گوگل
        for kw in config['keywords']:
            all_articles = fetch_rss(f"https://news.google.com/rss/search?q={quote(kw)}+when:1d&hl=fa&gl=IR&ceid=IR:fa")
            all_news.extend(all_articles)
            all_news.extend(search_google_x(kw))
        
        # پردازش و ارسال
        for news in all_news:
            h = hashlib.md5(news['link'].encode()).hexdigest()
            if not is_seen(h):
                final_title = gemini_rewrite(news['title'], config['name'])
                msg = f"📍 <b>خبر تازه: {config['name']}</b>\n\n🔹 {final_title}\n\n🔗 <a href='{news['link']}'>مشاهده منبع</a>"
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                             json={"chat_id": config['channel'], "text": msg, "parse_mode": "HTML"})
                mark_seen(h)
                time.sleep(2)

@app.route('/')
def home(): retur
