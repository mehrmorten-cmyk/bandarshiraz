import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from bs4 import BeautifulSoup
from flask import Flask
from datetime import datetime, timedelta

# تنظیمات لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OSINT_V45")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_TOPICS = ["اعتراضات", "امنیت", "خدمات شهری", "معیشت", "سلامت", "هواشناسی", "مدارس", "استخدام", "نیازمندی", "گمشده", "فرهنگی"]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "shiraz_online", "FouriFars"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bandarabbasnews", "akhbar_hormozgan", "hmd_news"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=15)

def clean_text(text):
    if not text: return ""
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;").strip()

def ai_tag(text, province):
    if not GEMINI_API_KEY: return "۱۱. عمومی"
    prompt = f"سردبیر {province} باش. از این لیست یک دسته انتخاب کن و یک تیتر ۵ کلمه ای بساز. فقط بنویس CAT | TITLE. لیست: {','.join(HUB_TOPICS)}. متن: {text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "گزارش جدید"

def scrape_tg_v45(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        logger.info(f"📡 @{user}: {len(msgs)} total messages found on web page.")
        
        now = datetime.now()
        for w in msgs:
            m = w.find("div", class_="tgme_widget_message")
            t_tag = w.find("time")
            if not m or not t_tag: continue
            
            # فیلتر زمان
            dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00')).replace(tzinfo=None)
            if now - dt > timedelta(hours=24):
                continue

            post_id = m.get("data-post")
            txt_div = m.find("div", class_="tgme_widget_message_text")
            body = txt_div.get_text(separator="\n").strip() if txt_div else ""
            
            media = None
            m_type = "text"
            video = m.find('video')
            if video: media = video.get('src'); m_type = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    match = re.search(r"url\('([^']+)'\)", photo.get('style', ''))
                    if match: media = match.group(1); m_type = "photo"
            
            if body: items.append({"text": body, "media": media, "type": m_type, "id": post_id})
    except Exception as e: logger.error(f"Scrape error @{user}: {e}")
    return items

def run_sync():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_v45 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit(); cur.close(); conn.close()
    except: pass

    for p_id, config in PROVINCES.items():
        logger.info(f"--- 🚀 SCANNING {config['name']} ---")
        for src in config['tg']:
            posts = scrape_tg_v45(src)
            fresh_count = len(posts)
            if fresh_count == 0:
                logger.info(f"ℹ️ @{src}: No fresh posts (last 24h) found.")
                continue

            for p in posts:
                h = hashlib.md5(str(p['id']).encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_v45 WHERE hash = %s", (h,))
                if not cur.fetchone():
                    res = ai_tag(p['text'], config['name'])
                    cap = f"<b>{clean_text(res)}</b>\n📍 استان {config['name']}\n\n{clean_text(p['text'][:850])}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                    
                    try:
                        tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        sent = False
                        if p['media']:
                            m_data = requests.get(p['media'], timeout=20).content
                            files = {('video' if p['type'] == "video" else 'photo'): m_data}
                            r = requests.post(tg_api + ("sendVideo" if p['type'] == "video" else "sendPhoto"), 
                                             data={"chat_id": config['channel'], "caption": cap, "parse_mode": "HTML"}, files=files)
                            sent = r.status_code == 200
                        
                        if not sent:
                            r = requests.post(tg_api+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                            sent = r.status_code == 200

                        if sent:
                            cur.execute("INSERT INTO seen_v45 (hash) VALUES (%s)", (h,))
                            conn.commit()
                            logger.info(f"✅ DISPATCHED: {p['id']}")
                    exceptException: pass
                cur.close(); conn.close()
                time.sleep(2)

@app.route('/check')
def check():
    threading.Thread(target=run_sync).start()
    return "OK"

@app.route('/')
def home(): return "SERVICE ACTIVE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
