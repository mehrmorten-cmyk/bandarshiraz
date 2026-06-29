import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from psycopg2 import pool
from bs4 import BeautifulSoup
from flask import Flask
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

# پیکربندی لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("OSINT_V51_FINAL")

# تنظیمات اصلی
BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ۱۱ موضوع استراتژیک
HUB_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و زیرساخت",
    "۴. 💰 معیشت و بازار", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

PROVINCES = {
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today"]
    },
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["shiraz_news", "akhbarfars", "YeRoozeShiraz", "shiraz_online", "Shiraz_Fouri"]
    }
}

app = Flask(__name__)
sync_lock = threading.Lock() # جلوگیری از تداخل اجرا

# پچ فنی ۱: سیستم اتصال پایدار
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 15, DATABASE_URL, sslmode='require', connect_timeout=10)
    logger.info("✅ Database Pool Initialized.")
except Exception as e:
    logger.critical(f"❌ DB Pool Critical Failure: {e}")

def get_content_hash(text):
    if not text: return None
    clean = "".join(re.sub(r'[^\w]', '', text).split())
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def ai_process(text, province):
    """تحلیل محتوا با متد JSON Mode"""
    if not GEMINI_API_KEY: return None
    prompt = f"سردبیر {province} باش. اگر خبر مربوط نیست فقط NO برگردان. وگرنه خروجی JSON: {{\"category\": \"...\", \"title\": \"...\"}}. لیست: {','.join(HUB_CATEGORIES)}. متن: {text[:500]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=12)
        res = r.json()['candidates'][0]['content']['parts'][0]['text']
        return None if "NO" in res.upper() else json.loads(res)
    except: return None

def scrape_telegram(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if resp.status_code != 200: return items
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        now_utc = datetime.now(timezone.utc)
        for w in reversed(msgs):
            try:
                m = w.find("div", class_="tgme_widget_message")
                t_tag = w.find("time")
                if not m or not t_tag: continue
                dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00'))
                if now_utc - dt > timedelta(hours=24): continue # فیلتر ۲۴ ساعته
                
                post_id = m.get("data-post")
                body = m.find("div", class_="tgme_widget_message_text").get_text(separator="\n").strip() if m.find("div", class_="tgme_widget_message_text") else ""
                if not body: continue
                
                media, m_type = None, "text"
                v = m.find('video')
                if v: media, m_type = v.get('src'), "video"
                else:
                    ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                    if ph:
                        match = re.search(r"url\('([^']+)'\)", ph.get('style', ''))
                        if match: media, m_type = match.group(1), "photo"
                
                items.append({"text": body, "media": media, "type": m_type, "id": post_id})
            except: continue
    except: pass
    return items

def run_osint_engine():
    if not sync_lock.acquire(blocking=False):
        logger.warning("🚫 Sync already in progress. Skipping.")
        return
    
    try:
        logger.info("🚀 OSINT PIPELINE STARTED")
        conn = db_pool.getconn()
        with conn:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS seen_v51 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        db_pool.putconn(conn)

        for p_id, config in PROVINCES.items():
            logger.info(f"🔎 Scanning: {config['name']}")
            for src in config['tg']:
                posts = scrape_telegram(src)
                for p in posts:
                    h = get_content_hash(p['text'])
                    
                    conn = db_pool.getconn()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1 FROM seen_v51 WHERE hash = %s", (h,))
                            if cur.fetchone(): 
                                db_pool.putconn(conn); continue
                        
                        ai_res = ai_analyze(p['text'], config['name'])
                        if not ai_res: # خبر رد شده یا بی ربط
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO seen_v51 VALUES (%s) ON CONFLICT DO NOTHING", (h,))
                            conn.commit()
                            db_pool.putconn(conn); continue
                        
                        # آماده سازی کپشن
                        source_url = f"https://t.me/{p['id']}"
                        cap = f"<b>{ai_res.get('category')}</b>\n📌 <b>{ai_res.get('title')}</b>\n\n{p['text'][:850]}\n\n🔗 <a href='{source_url}'>منبع</a>"
                        
                        # ارسال مدیا (استریمینگ)
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        sent = False
                        if p['media']:
                            try:
                                with requests.get(p['media'], stream=True, timeout=30) as r_stream:
                                    bio = io.BytesIO()
                                    for chunk in r_stream.iter_content(chunk_size=16384): bio.write(chunk)
                                    bio.seek(0)
                                    method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                                    bio.name = "file.mp4" if p['type'] == "video" else "file.jpg"
                                    requests.post(tg_url + method, data={"chat_id": config['channel'], "caption": cap, "parse_mode": "HTML"}, files={p['type']: bio}, timeout=45)
                                    sent = True
                            except: pass
                        
                        if not sent:
                            requests.post(tg_url + "sendMessage", json={"chat_id": config['channel'], "text": cap, "parse_mode": "HTML"}, timeout=20)
                        
                        with conn.cursor() as cur:
                            cur.execute("INSERT INTO seen_v51 VALUES (%s) ON CONFLICT DO NOTHING", (h,))
                        conn.commit()
                        db_pool.putconn(conn)
                        logger.info(f"✅ Dispatched: {p['id']}")
                        time.sleep(2)
                    except Exception as e:
                        logger.error(f"Post error: {e}")
                        db_pool.putconn(conn)
    finally:
        sync_lock.release()
        logger.info("🏁 OSINT PIPELINE FINISHED")

@app.route('/check')
@app.route('/')
def check():
    threading.Thread(target=run_osint_engine).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
