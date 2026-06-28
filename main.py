import os, json, time, psycopg2, logging, hashlib, threading, requests, re
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree
from flask import Flask, request

# پیکربندی اصلی
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "keywords": ["شیراز", "استان فارس", "مرودشت", "کازرون"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "keywords": ["بندرعباس", "هرمزگان", "قشم", "کیش"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    conn.commit(); cur.close(); conn.close()

def run_check():
    init_db()
    for p_id, config in PROVINCES.items():
        all_found = []
        # ۱. خبرگزاری‌ها
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                for i in root.findall(".//item")[:8]:
                    all_found.append({"title": i.findtext("title"), "link": i.findtext("link")})
            except: continue
        # ۲. تلگرام و ایکس (توییتر)
        for kw in config['keywords']:
            try:
                search_url = f"https://news.google.com/rss/search?q={quote(kw)}+site:t.me+OR+site:x.com+when:1d&hl=fa&gl=IR&ceid=IR:fa"
                root = ElementTree.fromstring(requests.get(search_url, timeout=10).content)
                for i in root.findall(".//item")[:5]:
                    all_found.append({"title": i.findtext("title"), "link": i.findtext("link")})
            except: continue

        for news in all_found:
            h = hashlib.md5(news['link'].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                txt = f"📍 <b>خبر {config['name']}</b>\n\n🔹 {news['title']}\n\n🔗 <a href='{news['link']}'>منبع</a>"
                kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                 json={"chat_id": config['channel'], "text": txt, "parse_mode": "HTML", "reply_markup": kb})
                if r.status_code == 200:
                    m_id = r.json()['result']['message_id']
                    cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                    cur.execute("INSERT INTO msg_logs VALUES (%s, %s, %s, %s, %s)", (h, config['channel'], str(m_id), news['title'], config['name']))
                    conn.commit()
            cur.close(); conn.close()

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
            prompt = f"خبر زیر از استان {prov} را با کلمات انقلابی (رژیم، قیام، کانون‌های شورشی) بازنویسی کن:\n{title}"
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            resp = requests.post(api_url, json={"contents": [{"parts": [{"text": prompt}]}]})
            new_txt = resp.json()['candidates'][0]['content']['parts'][0]['text']
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", 
                         json={"chat_id": c_id, "message_id": int(m_id), "text": f"✊ <b>نسخه مقاومت ({prov})</b>\n\n{new_txt.strip()}", "parse_mode": "HTML"})
        cur.close(); conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check Started"

@app.route('/')
def home(): return "Bot Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
