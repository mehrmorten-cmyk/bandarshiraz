import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree

# تنظیمات لاگ برای شفافیت کامل
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("FINAL_STABLE_V43")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_TOPICS = ["اعتراضات", "امنیت", "خدمات شهری", "معیشت", "سلامت", "هواشناسی", "مدارس", "استخدام", "نیازمندی", "گمشده", "فرهنگی"]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "shiraz_online", "FarsFouri", "FouriFars"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def clean_text(text):
    """پاکسازی متون برای جلوگیری از خطای HTML تلگرام"""
    if not text: return ""
    text = text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
    return text.strip()

def ai_tag(text, province):
    if not GEMINI_API_KEY: return "گزارش"
    prompt = f"سردبیر {province} باش. از این لیست یک دسته انتخاب کن و یک تیتر ۵ کلمه ای بساز. CAT | TITLE. لیست: {','.join(HUB_TOPICS)}. متن: {text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "گزارش جدید"

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        for w in reversed(msgs[-15:]):
            m = w.find("div", class_="tgme_widget_message")
            if not m or not m.get("data-post"): continue
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt_div = m.find("div", class_="tgme_widget_message_text")
            if txt_div: post["text"] = txt_div.get_text(separator="\n").strip()
            
            # پیدا کردن ویدیو یا عکس
            v = m.find('video')
            if v: 
                post["media"] = v.get('src'); post["type"] = "video"
            else:
                ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                if ph:
                    st = ph.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", st)
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: items.append(post)
    except Exception as e: logger.error(f"Scrape error @{user}: {e}")
    return items

def run_osint():
    # ساخت دیتابیس
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_v43 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit(); cur.close(); conn.close()
    except: pass

    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {config['name']} ---")
        pool = []
        for tg in config['tg']: pool.extend(scrape_tg(tg))
        # اضافه کردن RSS برای تضمین خبر در بندرعباس
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                for i in root.findall(".//item")[:5]:
                    pool.append({"text": i.findtext("title"), "id": i.findtext("link"), "type": "text", "media": None})
            except: continue

        for p in pool:
            # اثر انگشت محتوایی (بند ۴ توافق)
            content_hash = hashlib.md5(re.sub(r'\s+', '', p['text'][:60]).encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_v43 WHERE hash = %s", (content_hash,))
            if not cur.fetchone():
                res = ai_tag(p['text'], config['name'])
                safe_body = clean_text(p['text'][:850])
                cap = f"<b>{clean_text(res)}</b>\n📍 استان {config['name']}\n\n{safe_body}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع</a>"
                
                try:
                    tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    sent_ok = False
                    
                    # دانلود و آپلود فایل (حل خطای مدیا)
                    if p['media']:
                        media_data = requests.get(p['media'], timeout=20).content
                        files = {'video' if p['type'] == "video" else 'photo': media_data}
                        method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                        r = requests.post(tg_api + method, data={"chat_id": config['channel'], "caption": cap, "parse_mode": "HTML"}, files=files)
                        sent_ok = r.status_code == 200
                    
                    if not sent_ok:
                        r = requests.post(tg_api + "sendMessage", json={"chat_id": config['channel'], "text": cap, "parse_mode": "HTML"})
                        sent_ok = r.status_code == 200

                    if sent_ok:
                        cur.execute("INSERT INTO seen_v43 (hash) VALUES (%s)", (content_hash,))
                        conn.commit()
                        logger.info(f"✅ Dispatched: {p['id']}")
                except Exception as e: logger.error(f"Send error: {e}")
            cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_osint).start()
    return "OK"

@app.route('/')
def home(): return "RUNNING"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
