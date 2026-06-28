import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree
from urllib.parse import quote

# تنظیمات لاگ برای شفافیت کامل
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("FINAL_OSINT")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ۱۱ دسته بندی مورد تایید شما
HUB_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و زیرساخت",
    "۴. 💰 معیشت و بازار", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

# منابع عظیم اختصاصی شما
PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "sources": [
            "akhbarfars", "shiraz_news", "YeRoozeShiraz", "sums1401", "shiraztopnews",
            "FouriFars", "FarsFouri", "avaye_shiraz", "shirazu_twitter", "shiraz_news24",
            "shirazu1", "SaberinFars", "LineFars", "shiraz_salam", "Azad_shiraz",
            "Shiraz_us", "Fars_today", "eghtesadefars", "dorhamishiraziha", "Shiraz_Fouri",
            "shirazcute", "shiraztagram", "fars.online", "shiraz1400.ir", "shirazlover"
        ]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "sources": [
            "hormozgan_online", "bndonline", "akhbar_hormozgan", "hormozgan_today",
            "bandar_news", "bnd_wall", "bnd_job", "hormozgan.shat"
        ]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def init_db():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_v32 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit(); cur.close(); conn.close()
    except: pass

def ai_tag(text):
    """دسته بندی هوشمند خبر"""
    if not GEMINI_API_KEY: return "۱۱. عمومی"
    prompt = f"فقط نام دسته را بگو: {', '.join(HUB_CATEGORIES)}\nمتن: {text[:300]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۱۱. عمومی"

def scrape_tg(user):
    """متد اصلاح شده برای رصد تلگرام بدون بلاک شدن"""
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
            
            # استخراج مدیا (فیلم/عکس)
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
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {config['name']} ---")
        for src in config['sources']:
            posts = scrape_tg(src)
            for p in posts:
                h = hashlib.md5(str(p['id']).encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_v32 WHERE hash = %s", (h,))
                if not cur.fetchone():
                    tag = ai_tag(p['text'])
                    cap = f"<b>{tag}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                    
                    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    try:
                        res = None
                        if p['type'] == "video" and p['media']:
                            res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML"}, files={"video":("v.mp4", requests.get(p['media']).content)})
                        elif p['type'] == "photo" and p['media']:
                            res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                        else:
                            res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                        
                        if res and res.status_code == 200:
                            cur.execute("INSERT INTO seen_v32 (hash) VALUES (%s)", (h,))
                            conn.commit()
                            logger.info(f"✅ SENT: {p['id']}")
                    except: pass
                cur.close(); conn.close()
                time.sleep(1)

@app.route('/check')
def check():
    threading.Thread(target=run_osint).start()
    return "OSINT ENGINE V32 STARTED."

@app.route('/')
def home(): return "V32 ONLINE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
