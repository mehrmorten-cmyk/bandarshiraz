import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree

# تنظیمات لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("HUB_DIAGNOSTIC")

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
        "sources": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "sums1401", "shiraz_online", "shiraz_ma"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "sources": ["hormozgan_online", "bndonline", "akhbar_hormozgan", "hormozgan_today"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def init_db():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_v33 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit(); cur.close(); conn.close()
    except: pass

def ai_tag(text):
    if not GEMINI_API_KEY: return "۱۱. عمومی"
    prompt = f"فقط نام دسته را بگو: {', '.join(HUB_CATEGORIES)}\nمتن: {text[:300]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۱۱. عمومی"

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        for w in reversed(msgs[-10:]):
            m = w.find("div", class_="tgme_widget_message")
            if not m or not m.get("data-post"): continue
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
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

def run_osint():
    init_db()
    tg_base = f"https://api.telegram.org/bot{BOT_TOKEN}/"
    
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {config['name']} ---")
        # تست اتصال اولیه به کانال
        requests.post(tg_base + "sendMessage", json={"chat_id": config['channel'], "text": "🔄 در حال بررسی منابع جدید..."})
        
        for src in config['sources']:
            posts = scrape_tg(src)
            for p in posts:
                h = hashlib.md5(str(p['id']).encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_v33 WHERE hash = %s", (h,))
                if not cur.fetchone():
                    tag = ai_tag(p['text'])
                    cap = f"<b>{tag}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                    
                    try:
                        res = None
                        if p['type'] == "video" and p['media']:
                            logger.info(f"Downloading video for {p['id']}...")
                            v_data = requests.get(p['media'], timeout=30).content
                            res = requests.post(tg_base+"sendVideo", data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML"}, files={"video":("v.mp4", v_data)})
                        elif p['type'] == "photo" and p['media']:
                            res = requests.post(tg_base+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                        
                        # اگر ارسال مدیا شکست خورد یا متن خالی بود، پیام متنی ساده بفرست
                        if not res or res.status_code != 200:
                            logger.warning(f"Media failed, sending text only for {p['id']}")
                            res = requests.post(tg_base+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                        
                        logger.info(f"TG Response for {p['id']}: {res.text}")
                        
                        if res.status_code == 200:
                            cur.execute("INSERT INTO seen_v33 (hash) VALUES (%s)", (h,))
                            conn.commit()
                    except Exception as e:
                        logger.error(f"Send Error for {p['id']}: {e}")
                cur.close(); conn.close()
                time.sleep(2)

@app.route('/check')
def check():
    threading.Thread(target=run_osint).start()
    return "Check started. Watch logs for direct TG responses."

@app.route('/')
def home(): return "V33 ONLINE - Diagnostic Mode"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
