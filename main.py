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
from flask import Flask, request

# تنظیمات
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# منابع استخراج شده توسط ایجنت (فارس و هرمزگان)
PROVINCES = {
    "fars": {
        "name": "فارس",
        "channel": os.environ.get("CHANNEL_ID_FARS"),
        "keywords": ["شیراز", "استان فارس", "مرودشت", "کازرون", "جهرم", "فسا", "داراب"],
        "rss": [
            "https://www.irna.ir/rss/service/131",
            "https://www.tasnimnews.com/fa/rss/service/0/8",
            "https://www.mehrnews.com/rss/service/74",
            "https://www.isna.ir/rss/service/67"
        ]
    },
    "hormozgan": {
        "name": "هرمزگان",
        "channel": os.environ.get("CHANNEL_ID_HORMOZGAN"),
        "keywords": ["بندرعباس", "هرمزگان", "قشم", "کیش", "میناب", "بندرلنگه"],
        "rss": [
            "https://www.irna.ir/rss/service/151",
            "https://www.tasnimnews.com/fa/rss/service/0/13",
            "https://www.mehrnews.com/rss/service/84",
            "https://www.isna.ir/rss/service/77"
        ]
    }
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, original_title TEXT, province TEXT)")
    conn.commit()
    cur.close()
    conn.close()

def run_news_check():
    init_db()
    for p_id, config in PROVINCES.items():
        if not config['channel']: continue
        
        all_found = []
        # ۱. استخراج از RSS های خبرگزاری‌ها
        for url in config['rss']:
            try:
                resp = requests.get(url, timeout=10)
                root = ElementTree.fromstring(resp.content)
                for item in root.findall(".//item")[:10]:
                    all_found.append({"title": item.findtext("title"), "link": item.findtext("link")})
            except: continue

        # ۲. جستجو در تلگرام و اینستاگرام (از طریق گوگل)
        for kw in config['keywords']:
            try:
                search_url = f"https://news.google.com/rss/search?q={quote(kw)}+site:t.me+OR+site:instagram.com+when:1d&hl=fa&gl=IR&ceid=IR:fa"
                root = ElementTree.fromstring(requests.get(search_url).content)
                for item in root.findall(".//item")[:5]:
                    all_found.append({"title": item.findtext("title"), "link": item.findtext("link")})
            except: continue

        # ارسال به تلگرام با دکمه بازنویسی
        for news in all_found:
            h = hashlib.md5(news['link'].encode()).hexdigest()
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                txt = f"📍 <b>خبر {config['name']}</b>\n\n🔹 {news['title']}\n\n🔗 <a href='{news['link']}'>مشاهده منبع</a>"
                kb = {"inline_keyboard": [[{"text": "📝 بازنویسی با پروتکل مقاومت", "callback_data": f"rw:{h}"}]]}
                
                tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                r = requests.post(tg_url, json={"chat_id": config['channel'], "text": txt, "parse_mode": "HTML", "reply_markup": kb})
                
                if r.status_code == 200:
                    msg_id = r.json()['result']['message_id']
                    cur.execute("INSERT INTO seen_news (hash) VALUES (%s)", (h,))
                    cur.execute("INSERT INTO msg_logs (hash, channel_id, msg_id, original_title, province) VALUES (%s, %s, %s, %s, %s)", 
                                (h, str(config['channel']), str(msg_id), news['title'], config['name']))
                    conn.commit()
            cur.close()
            conn.close()
            time.sleep(1)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "callback_query" in data:
        cb = data["callback_query"]
        if cb["data"].startswith("rw:"):
            h = cb["data"][3:]
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT original_title, channel_id, msg_id, province FROM msg_logs WHERE hash = %s", (h,))
            row = cur.fetchone()
            if row:
                title, c_id, m_id, prov = row
                # فراخوان Gemini برای بازنویسی
                prompt = f"خبر زیر از استان {prov} را با کلمات انقلابی (رژیم، قیام، کانون‌های شورشی) بازنویسی کن. فقط متن نهایی را بده:\n{title}"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
                new_txt = resp.json()['candidates'][0]['content']['parts'][0]['text']
                
                final_msg = f"✊ <b>نسخه بازنویسی شده ({prov})</b>\n\n📌 {new_txt.strip()}\n\n✅ <i>تایید شده توسط پروتکل مقاومت</i>"
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", 
                             json={"chat_id": c_id, "message_id": int(m_id), "text": final_msg, "parse_mode": "HTML"})
            cur.close()
            conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_news_check).start()
    return "Check Started"

@app.route('/')
def home(): return "Bot Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
