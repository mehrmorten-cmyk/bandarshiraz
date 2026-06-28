import os, json, time, psycopg2, logging, hashlib, threading, requests, re
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree
from bs4 import BeautifulSoup
from flask import Flask, request

# تنظیمات
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# منابع شناسایی شده توسط ایجنت
PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "tg_sources": ["shiraz_online", "akhbarshiraz", "fars_news_fars", "asrshiraz"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "tg_sources": ["hormozgan_online", "akhbar_hormozgan", "bndonline", "hmd_news"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def ai_filter(title, province_name):
    """فیلتر هوشمند برای حذف اخبار غیرمرتبط (آذربایجان، ورزشی و غیره)"""
    if not GEMINI_API_KEY: return True
    prompt = f"آیا این خبر مستقیماً مربوط به وقایع استان {province_name} است؟ فقط بنویس YES یا NO.\nخبر: {title}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return "YES" in resp.json()['candidates'][0]['content']['parts'][0]['text'].upper()
    except: return True

def scrape_telegram(tg_user):
    """رصد مستقیم محتوای وب کانال تلگرام بدون نیاز به API"""
    news_items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        resp = requests.get(url, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message_wrap')
        for msg in messages[-5:]: # ۵ پیام آخر
            text_area = msg.find('div', class_='tgme_widget_message_text')
            if text_area:
                title = text_area.get_text()[:100] + "..."
                link = "https://t.me/" + tg_user # لینک کانال مرجع
                news_items.append({"title": title, "link": link + "/" + msg.find('div', class_='tgme_widget_message')['data-post'].split('/')[-1]})
    except: pass
    return news_items

def run_check():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        all_news = []
        # ۱. رصد مستقیم تلگرام
        for tg in config['tg_sources']:
            all_news.extend(scrape_telegram(tg))
        
        # ۲. رصد خبرگزاری‌ها (RSS)
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                for i in root.findall(".//item")[:5]:
                    all_news.append({"title": i.findtext("title"), "link": i.findtext("link")})
            except: continue

        for news in all_news:
            h = hashlib.md5(news['title'].encode()).hexdigest() # هش بر اساس تیتر برای تلگرام
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                # فیلتر هوشمند قبل از ارسال
                if ai_filter(news['title'], config['name']):
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
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"], "text": "⏳ در حال بازنویسی..."})
            prompt = f"این خبر را با پروتکل مقاومت و واژگان انقلابی (رژیم، قیام، کانون‌های شورشی) بازنویسی کن. متن نهایی:\n{title}"
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
    return "Started"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
