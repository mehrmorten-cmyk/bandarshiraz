import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("HUB_PLATINUM_V37")

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
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars", "shiraz_online"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13", "https://www.isna.ir/rss/service/77"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def extract_web_image(url):
    """استخراج عکس اصلی از صفحات خبرگزاری‌ها (og:image)"""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, 'html.parser')
        img = soup.find("meta", property="og:image")
        if img: return img.get("content")
    except: return None
    return None

def ai_tag(text):
    if not GEMINI_API_KEY: return "۱۱. عمومی"
    prompt = f"فقط نام دقیق یکی از این ۱۱ دسته را انتخاب کن: {', '.join(HUB_CATEGORIES)}\nمتن: {text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۱۱. عمومی"

def scrape_tg_pro(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        now = datetime.now()
        for w in msgs[-20:]:
            m = w.find("div", class_="tgme_widget_message")
            t_tag = w.find("time")
            if not m or not t_tag: continue
            dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00')).replace(tzinfo=None)
            if now - dt > timedelta(hours=24): continue
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post"), "link": f"https://t.me/{m.get('data-post')}"}
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

def run_final_sync():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_v37 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        logger.info(f"--- 🛰 OSINT START: {config['name']} ---")
        pool = []
        for src in config['tg']: pool.extend(scrape_tg_pro(src))
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                for i in root.findall(".//item")[:10]:
                    link = i.findtext("link")
                    img = extract_web_image(link)
                    pool.append({"text": i.findtext("title"), "id": link, "type": "photo" if img else "text", "media": img, "link": link})
            except: continue

        for p in pool:
            h = hashlib.md5(str(p['id']).encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_v37 WHERE hash = %s", (h,))
            if not cur.fetchone():
                tag = ai_tag(p['text'])
                cap = f"<b>{tag}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='{p['link']}'>منبع خبر</a>"
                try:
                    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    res = None
                    if p['type'] == "video" and p['media']:
                        res = requests.post(tg_url+"sendVideo", json={"chat_id":config['channel'], "video":p['media'], "caption":cap, "parse_mode":"HTML"})
                    elif p.get('media'):
                        res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                    else:
                        res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                    
                    if res and res.status_code == 200:
                        cur.execute("INSERT INTO seen_v37 (hash) VALUES (%s)", (h,))
                        conn.commit()
                        logger.info(f"✅ DISPATCHED: {p['id']}")
                except: pass
            cur.close(); conn.close()
            time.sleep(2)

@app.route('/check')
def check():
    threading.Thread(target=run_final_sync).start()
    return "Platinum Hub Syncing with Web Images..."

@app.route('/')
def home(): return "Province Hub Platinum v37 Active"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
