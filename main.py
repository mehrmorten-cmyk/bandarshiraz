import os, json, time, psycopg2, logging, hashlib, threading, requests, re
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree
from flask import Flask, request

# پیکربندی
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "keywords": ["شیراز", "فارس"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "keywords": ["بندرعباس", "هرمزگان"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13"]
    }
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    conn.commit(); cur.close(); conn.close()

def run_check():
    init_db()
    print("🚀 شروع عملیات جستجوی اخبار...")
    for p_id, config in PROVINCES.items():
        all_found = []
        print(f"🔎 در حال بررسی منابع استان {config['name']}...")
        
        # ۱. خبرگزاری‌ها
        for url in config['rss']:
            try:
                resp = requests.get(url, timeout=10)
                root = ElementTree.fromstring(resp.content)
                items = root.findall(".//item")
                print(f"--- منبع {url[:30]}... : {len(items)} خبر یافت شد.")
                for i in items[:10]:
                    all_found.append({"title": i.findtext("title"), "link": i.findtext("link")})
            except Exception as e: print(f"❌ خطا در فید: {e}")

        # ۲. گوگل نیوز (تلگرام و وب)
        for kw in config['keywords']:
            try:
                search_url = f"https://news.google.com/rss/search?q={quote(kw)}+when:1h&hl=fa&gl=IR&ceid=IR:fa"
                root = ElementTree.fromstring(requests.get(search_url, timeout=10).content)
                items = root.findall(".//item")
                print(f"--- گوگل نیوز ({kw}): {len(items)} خبر تازه یافت شد.")
                for i in items[:5]:
                    all_found.append({"title": i.findtext("title"), "link": i.findtext("link")})
            except: continue

        if not all_found:
            print(f"📭 خبری برای {config['name']} در این ساعت یافت نشد.")
            continue

        for news in all_found:
            h = hashlib.md5(news['link'].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                print(f"📤 ارسال خبر جدید: {news['title'][:50]}")
                txt = f"📍 <b>خبر {config['name']}</b>\n\n🔹 {news['title']}\n\n🔗 <a href='{news['link']}'>مشاهده منبع</a>"
                kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                 json={"chat_id": config['channel'], "text": txt, "parse_mode": "HTML", "reply_markup": kb})
                if r.status_code == 200:
                    m_id = r.json()['result']['message_id']
                    cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                    cur.execute("INSERT INTO msg_logs VALUES (%s, %s, %s, %s, %s)", (h, config['channel'], str(m_id), news['title'], config['name']))
                    conn.commit()
            cur.close(); conn.close()
    print("✅ عملیات جستجو به پایان رسید.")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "callback_query" in data:
        cb = data["callback_query"]; h = cb["data"][3:]
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT title, channel_id, msg_id, prov FROM msg_logs WHERE hash = %s", (h,))
        row = cur.fetchone()
        if row:
            title, c_id, m_id, prov = row
            # نمایش حالت در حال تایپ برای ادمین
            requests.post(f"http
