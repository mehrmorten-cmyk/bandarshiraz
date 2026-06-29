import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from psycopg2 import pool
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

# پیکربندی حرفه‌ای لاگ
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("PRO_OSINT_V48")

# تنظیمات اصلی
BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و زیرساخت",
    "۴. 💰 معیشت و بازار", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

# لیست کامل منابع اختصاصی شما
PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "sums1401", "shiraztopnews", "FouriFars", "FarsFouri", "avaye_shiraz", "shirazu_twitter", "shiraz_news24", "shiraz_salam", "Azad_shiraz", "Shiraz_us", "dorhamishiraziha", "Shiraz_Fouri"],
        "queries": ["شیراز اعتراض", "فسا حادثه", "مرودشت قطعی برق", "کازرون معیشت", "شیراز دیوار"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall", "bnd_job"],
        "queries": ["بندرعباس قطعی برق", "قشم اعتراض", "کیش گرانی", "بندرلنگه حادثه", "هرمزگان معیشت"]
    }
}

app = Flask(__name__)

# ۱. پچ فنی: Connection Pool برای جلوگیری از نشت دیتابیس
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, DATABASE_URL, sslmode='require', connect_timeout=15)
    logger.info("✅ Database Connection Pool initialized.")
except Exception as e:
    logger.critical(f"❌ DB Pool Error: {e}")
    db_pool = None

# ۲. پچ فنی: Retry Logic برای پایداری
def with_db_retry(retries=3, backoff=2):
    def decorator(func):
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts <= retries:
                try: return func(*args, **kwargs)
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                    if attempts == retries: raise e
                    time.sleep(backoff * (2 ** attempts))
                    attempts += 1
            return None
        return wrapper
    return decorator

# ۳. پچ فنی: Content-Based Hashing
def generate_content_hash(text):
    if not text: return None
    normalized = re.sub(r'[^\w\s]', '', text)
    normalized = "".join(normalized.split())
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()

def clean_html_safe(text):
    if not text: return ""
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

# ۴. پچ فنی: AI JSON Mode
def ai_analyze(text, province):
    if not GEMINI_API_KEY: return None
    prompt = f"""تحلیلگر استان {province} باش. اگر خبر مربوط نیست فقط کلمه NO را برگردان.
    اگر هست، دسته بندی کن و تیتر ۶ کلمه ای بساز. خروجی فقط JSON:
    {{"category": "...", "title": "..."}}
    لیست: {', '.join(HUB_CATEGORIES)}
    متن: {text[:500]}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }, timeout=12)
        res_text = r.json()['candidates'][0]['content']['parts'][0]['text']
        if "NO" in res_text.upper(): return None
        return json.loads(res_text)
    except: return None

def scrape_tg_resilient(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        now_utc = datetime.now(timezone.utc)
        for w in reversed(msgs):
            try:
                m = w.find("div", class_="tgme_widget_message")
                t_tag = w.find("time")
                if not m or not t_tag: continue
                dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00'))
                if now_utc - dt > timedelta(hours=24): continue
                pid = m.get("data-post")
                txt_div = m.find("div", class_="tgme_widget_message_text")
                body = txt_div.get_text(separator="\n").strip() if txt_div else ""
                media, m_type = None, "text"
                v = m.find('video')
                if v: media, m_type = v.get('src'), "video"
                else:
                    ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                    if ph:
                        match = re.search(r"url\('([^']+)'\)", ph.get('style', ''))
                        if match: media, m_type = match.group(1), "photo"
                if body: items.append({"text": body, "media": media, "type": m_type, "id": pid})
            except: continue
    except: pass
    return items

def universal_search(query):
    items = []
    try:
        url = f"https://news.google.com/rss/search?q={quote(query)}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
        root = ElementTree.fromstring(requests.get(url, timeout=15).content)
        for i in root.findall(".//item")[:10]:
            items.append({"text": i.findtext("title"), "id": i.findtext("link"), "type": "text", "media": None})
    except: pass
    return items

@with_db_retry()
def is_seen(h):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM seen_v48 WHERE hash = %s", (h,))
            return cur.fetchone() is not None
    finally: db_pool.putconn(conn)

@with_db_retry()
def mark_seen(h):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO seen_v48 (hash) VALUES (%s) ON CONFLICT (hash) DO NOTHING", (h,))
            conn.commit()
    finally: db_pool.putconn(conn)

def run_v48_engine():
    if not db_pool: return
    # پچ فنی: شروع امن با بستن Cursor
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS seen_v48 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
            conn.commit()
    except Exception as e: logger.error(f"Init DB Error: {e}")
    finally: db_pool.putconn(conn)

    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 OSINT START: {config['name']} ---")
        pool = []
        for src in config['tg']: pool.extend(scrape_tg_resilient(src))
        for q in config['queries']: pool.extend(universal_search(q))

        for p in pool:
            h = generate_content_hash(p['text'])
            if not h or is_seen(h): continue
            
            ai_res = ai_analyze(p['text'], config['name'])
            
            # پچ فنی: ذخیره اخبار رد شده برای جلوگیری از مصرف بیهوده توکن
            if not ai_res:
                mark_seen(h)
                continue
            
            # پچ فنی: لینک‌دهی صحیح منبع
            raw_id = str(p['id'])
            source_url = raw_id if raw_id.startswith('http') else f"https://t.me/{raw_id}"
            
            safe_cap = f"<b>{clean_html_safe(ai_res.get('category'))}</b>\n📌 <b>{clean_html_safe(ai_res.get('title'))}</b>\n\n{clean_html_safe(p['text'][:850])}\n\n🔗 <a href='{source_url}'>منبع خبر</a>"
            
            try:
                tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                sent = False
                # پچ فنی: استریمینگ مدیا برای رم ۵۱۲ مگابایتی
                if p['media']:
                    try:
                        with requests.get(p['media'], stream=True, timeout=30) as r_stream:
                            bio = io.BytesIO()
                            for chunk in r_stream.iter_content(chunk_size=16384): bio.write(chunk)
                            bio.seek(0)
                            method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                            bio.name = f"file.{'mp4' if p['type'] == 'video' else 'jpg'}"
                            r = requests.post(tg_api + method, data={"chat_id": config['channel'], "caption": safe_cap, "parse_mode": "HTML"}, files={p['type']: bio}, timeout=40)
                            sent = r.status_code == 200
                    except: pass
                
                if not sent:
                    r = requests.post(tg_api + "sendMessage", json={"chat_id": config['channel'], "text": safe_cap, "parse_mode": "HTML"}, timeout=15)
                    sent = r.status_code == 200
                
                if sent: mark_seen(h)
            except: continue
            time.sleep(2)

@app.route('/check')
def check():
    threading.Thread(target=run_v48_engine).start()
    return "V48 ENGINE: ONLINE", 200

@app.route('/')
def home(): return "V48 STABLE", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
