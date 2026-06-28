import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OSINT_V34")

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
        "sources": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars", "shiraz_online", "shirazu_twitter"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8", "https://www.mehrnews.com/rss/service/74"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "sources": ["hormozgan_online", "bndonline", "akhbar_hormozgan", "hormozgan_today", "bandarabbasnews", "bnd_city"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13", "https://www.mehrnews.com/rss/service/84"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def init_db():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_v34 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, timeout=20, headers=headers)
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        for w in reversed(msgs[-15:]):
            m = w.find("div", class_="tgme_widget_message")
            if not m or not m.get("data-post"): continue
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post"), "link": f"https://t.me/{m.get('data-post')}"}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            video = m.find('video')
            if video: 
                post["media"] = video.get('src'); post["type"] = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    st = photo.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", st)
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: items.append(post)
    except: pass
    return items

def run_osint():
    init_db()
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {config['name']} ---")
        pool = []
        # ۱. تلگرام اختصاصی
        for src in config['sources']: pool.extend(scrape_tg(src))
        
        # ۲. خبرگزاری های سراسری (جایگزین گوگل برای پایداری)
        for url in config['rss']:
            try:
                resp = requests.get(url, timeout=15)
                root = ElementTree.fromstring(resp.content)
                for i in root.findall(".//item")[:10]:
                    pool.append({"text": i.findtext("title"), "id": i.findtext("link"), "type": "text", "media": None, "link": i.findtext("link")})
            except: continue

        for p in pool:
            h = hashlib.md5(str(p['id']).encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_v34 WHERE hash = %s", (h,))
            if not cur.fetchone():
                tag = ai_tag(p['text'])
                cap = f"<b>{tag}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='{p['link']}'>منبع خبر</a>"
                
                try:
                    res = None
                    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    headers = {"User-Agent": "Mozilla/5.0"}
                    
                    if p['type'] == "video" and p['media']:
                        v_data = requests.get(p['media'], headers=headers).content
                        res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML"}, files={"video":("v.mp4", v_data)})
                    elif p['type'] == "photo" and p['media']:
                        res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                    
                    if not res or res.status_code != 200:
                        res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                    
                    if res.status_code == 200:
                        cur.execute("INSERT INTO seen_v34 (hash) VALUES (%s)", (h,))
                        conn.commit()
                        logger.info(f"✅ SUCCESS: {p['id']}")
                except: pass
            cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_osint).start()
    return "OSINT Engine v34 Syncing..."

@app.route('/')
def home(): return "V34 ONLINE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
