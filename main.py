import os, json, time, psycopg2, logging, hashlib, threading, requests, re, sys
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree
from bs4 import BeautifulSoup
from flask import Flask, request

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "local_keys": ["شیراز", "فارس", "مرودشت", "کازرون", "جهرم", "لارستان"],
        "tg_sources": ["shiraz_online", "akhbarshiraz", "asrshiraz"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "local_keys": ["بندرعباس", "هرمزگان", "قشم", "کیش", "میناب", "جاسک"],
        "tg_sources": ["hormozgan_online", "akhbar_hormozgan", "bndonline"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def ai_gatekeeper(title, province_name):
    """قاضی هوش مصنوعی: حذف اخبار غیرمرتبط کشوری یا استانی دیگر"""
    if not GEMINI_API_KEY: return True
    prompt = f"آیا این خبر مستقیماً مربوط به حوادث، اخبار یا تحولات استان {province_name} است؟ اگر مربوط به استان دیگری یا خبر ملی/بین‌المللی است، فقط بگو NO. اگر مربوط به {province_name} است بگو YES.\nخبر: {title}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        res = resp.json()['candidates'][0]['content']['parts'][0]['text'].upper()
        return "YES" in res
    except: return True

def scrape_tg(tg_user):
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        resp = requests.get(url, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message_text')
        for msg in messages[-5:]:
            txt = msg.get_text(separator=" ").strip()
            if len(txt) > 30: items.append({"title": txt[:250], "link": f"https://t.me/s/{tg_user}"})
    except: pass
    return items

def run_check():
    logging.info("🚀 شروع پایش هوشمند اخبار...")
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        all_news = []
        # ۱. رصد تلگرام
        for tg in config['tg_sources']: all_news.extend(scrape_tg(tg))
        
        # ۲. رصد خبرگزاری‌ها
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                for i in root.findall(".//item")[:10]:
                    all_news.append({"title": i.findtext("title"), "link": i.findtext("link")})
            except: continue

        for news in all_news:
            # الف: فیلتر متنی سخت‌گیرانه (حذف اخبار آذربایجان، خوی و غیره)
            if not any(key in news['title'] for key in config['local_keys']):
                continue

            h = hashlib.md5(news['title'].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                # ب: فیلتر هوش مصنوعی برای اطمینان ۱۰۰٪
                if ai_gatekeeper(news['title'], config['name']):
                    logging.info(f"📤 ارسال خبر تایید شده: {news['title'][:50]}")
                    txt = f"📍 <b>خبر تازه ({config['name']})</b>\n\n🔹 {news['title']}\n\n🔗 <a href='{news['link']}'>منبع اصلی</a>"
                    kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                     json={"chat_id": config['channel'], "text": txt, "parse_mode": "HTML", "reply_markup": kb})
                    if r.status_code == 200:
                        cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                        cur.execute("INSERT INTO msg_logs VALUES (%s, %s, %s, %s, %s)", 
                                   (h, config['channel'], str(r.json()['result']['message_id']), news['title'], config['name']))
                        conn.commit()
            cur.close(); conn.close()
    logging.info("🏁 پایان پایش.")

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    re
