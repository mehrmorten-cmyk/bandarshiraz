import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("STRICT_NEWS_V35")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و زیرساخت",
    "۴. 💰 معیشت و بازار", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "shiraz_online", "FouriFars", "shiraz_ma"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def ai_tag(text):
    if not GEMINI_API_KEY: return "۱۱. عمومی"
    prompt = f"فقط نام دسته را بگو: {', '.join(HUB_CATEGORIES)}\nمتن: {text[:300]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۱۱. عمومی"

def scrape_tg_fresh(user):
    """استخراج فقط اخبار ۲۴ ساعت اخیر"""
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        
        now = datetime.now()
        for w in msgs:
            m = w.find("div", class_="tgme_widget_message")
            time_tag = w.find("time")
            if not m or not time_tag: continue
            
            # چک کردن تاریخ (بسیار مهم)
            msg_date = time_tag.get("datetime") # فرمت: 2024-06-28T18:45:00+00:00
            dt = datetime.fromisoformat(msg_date.replace('Z', '+00:00')).replace(tzinfo=None)
            
            # اگر پیام قدیمی‌تر از ۲۴ ساعت بود، ردش کن
            if now - dt > timedelta(hours=24): continue

            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt_div = m.find("div", class_="tgme_widget_message_text")
            if txt_div: post["text"] = txt_div.get_text(separator="\n").strip()
            
            v = m.find('video')
            if v: post["media"] = v.get('src'); post["type"] = "video"
            else:
                ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                if ph:
                    st = ph.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", st)
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            
            if post["text"]: items.append(post)
    except: pass
    return items

def run_sync():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_v35 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {p_id.upper()} ---")
        for src in config['tg']:
            posts = scrape_tg_fresh(src)
            for p in posts:
                h = hashlib.md5(str(p['id']).encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_v35 WHERE hash = %s", (h,))
                if not cur.fetchone():
                    tag = ai_tag(p['text'])
                    cap = f"<b>{tag}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                    
                    try:
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        res = None
                        if p['type'] == "video" and p['media']:
                            v_data = requests.get(p['media'], timeout=20).content
                            res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML"}, files={"video":("v.mp4", v_data)})
                        elif p['type'] == "photo" and p['media']:
                            res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                        
                        if not res or res.status_code != 200:
                            res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                        
                        if res.status_code == 200:
                            cur.execute("INSERT INTO seen_v35 (hash) VALUES (%s)", (h,))
                            conn.commit()
                            logger.info(f"✅ SENT FRESH: {p['id']}")
                    except: pass
                cur.close(); conn.close()
                time.sleep(1)

@app.route('/check')
def check():
    threading.Thread(target=run_sync).start()
    return "Strict Time Sync Started (24h limit)."

@app.route('/')
def home(): return "Fresh News Engine Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
