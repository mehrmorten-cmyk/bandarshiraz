import os, json, time, psycopg2, logging, hashlib, threading, requests, re, sys
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree
from bs4 import BeautifulSoup
from flask import Flask, request

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# تعریف واژگان بحران برای شکار اخبار معیشتی و اجتماعی
CRISIS_KEYWORDS = ["اعتراض", "تجمع", "معیشت", "گرما", "قطعی برق", "حادثه", "ویدیو", "بحران", "دانش آموز"]

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "search_keys": ["شیراز", "استان فارس", "صدای شیراز"],
        "tg_sources": ["shiraz_ma", "shiraz_online", "shiraz_it", "shirazi_ha"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.isna.ir/rss/service/67"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "search_keys": ["بندرعباس", "هرمزگان", "بندری ها", "صدای هرمزگان"],
        "tg_sources": ["bndonline", "hmd_news", "hormozgan_today", "khabor_hormozgan"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.isna.ir/rss/service/77"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def ai_smart_filter(title, province):
    """هوش مصنوعی: شناسایی اخبار داغ اجتماعی و حذف موارد بی‌ربط"""
    if not GEMINI_API_KEY: return True
    prompt = f"آیا این خبر مربوط به یک رویداد اجتماعی، معیشتی، اعتراضی یا حادثه مهم در استان {province} است؟ فقط YES یا NO بنویس.\nخبر: {title}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return "YES" in resp.json()['candidates'][0]['content']['parts'][0]['text'].upper()
    except: return True

def scrape_tg_web(tg_user):
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        soup = BeautifulSoup(requests.get(url, timeout=15).text, 'html.parser')
        msgs = soup.find_all('div', class_='tgme_widget_message_text')
        for m in msgs[-8:]:
            t = m.get_text(separator=" ").strip()
            if len(t) > 30: items.append({"title": t[:300], "link": f"https://t.me/s/{tg_user}"})
    except: pass
    return items

def run_deep_check():
    logging.info("🕵️ شروع پایش عمیق (Deep Search) برای اخبار اجتماعی...")
    init_db()
    
    for p_id, config in PROVINCES.items():
        findings = []
        
        # ۱. رصد مستقیم کانال‌های مردمی تلگرام
        for tg in config['tg_sources']: findings.extend(scrape_tg_web(tg))
        
        # ۲. جستجوی ترکیبی کلمات بحران در گوگل و شبکه های اجتماعی
        for sk in config['search_keys']:
            for ck in CRISIS_KEYWORDS:
                query = f'"{sk}" {ck} when:1d'
                try:
                    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=fa&gl=IR&ceid=IR:fa"
                    root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                    for i in root.findall(".//item")[:5]:
                        findings.append({"title": i.findtext("title"), "link": i.findtext("link")})
                except: continue

        # ۳. فیلتر و ارسال
        for news in findings:
            h = hashlib.md5(news['title'].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                if ai_smart_filter(news['title'], config['name']):
                    logging.info(f"🔥 خبر داغ یافت شد: {news['title'][:50]}")
                    txt = f"🚨 <b>گزارش خبری: {config['name']}</b>\n\n📌 {news['title']}\n\n🔗 <a href='{news['link']}'>مشاهده منبع محلی</a>"
                    kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                                     json={"chat_id": config['channel'], "text": txt, "parse_mode": "HTML", "reply_markup": kb})
                    if r.status_code == 200:
                        cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                        cur.execute("INSERT INTO msg_logs VALUES (%s, %s, %s, %s, %s)", 
                                   (h, config['channel'], str(r.json()['result']['message_id']), news['title'], config['name']))
                        conn.commit()
            cur.close(); conn.close()

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    conn.commit(); cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_deep_check).start()
    return "Deep Scan Started. Searching for social and crisis news..."

@app.route('/webhook', methods=['POST'])
def webhook():
    # همان منطق قبلی برای بازنویسی
    pass

@app.route('/')
def home(): return "Social Scraper Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
