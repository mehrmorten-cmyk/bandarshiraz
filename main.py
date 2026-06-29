import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from psycopg2 import pool
from bs4 import BeautifulSoup
from flask import Flask
from datetime import datetime, timedelta, timezone

# پیکربندی لاگ
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("HUB_OSINT_V53")

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

PROVINCES = {
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today"]
    },
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars", "shiraz_online"]
    }
}

app = Flask(__name__)
sync_lock = threading.Lock()

# مدیریت اتصال دیتابیس
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 15, DATABASE_URL, sslmode='require', connect_timeout=15)
except Exception as e:
    logger.critical(f"Database Pool Failure: {e}")

def with_db_retry(retries=3):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for i in range(retries + 1):
                try: return func(*args, **kwargs)
                except:
                    if i == retries: return None
                    time.sleep(2)
            return None
        return wrapper
    return decorator

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
            cur.execute("INSERT INTO seen_v48 (hash) VALUES (%s) ON CONFLICT DO NOTHING", (h,))
            conn.commit()
    finally: db_pool.putconn(conn)

def ai_analyze_content(text, province):
    """تحلیل هوشمند محتوا با خروجی JSON"""
    if not GEMINI_API_KEY: return None
    prompt = f"سردبیر {province} باش. اگر خبر مربوط نیست فقط NO برگردان. وگرنه خروجی JSON: {{\"category\": \"...\", \"title\": \"...\"}}. لیست: {','.join(HUB_CATEGORIES)}. متن: {text[:500]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=12)
        res = r.json()['candidates'][0]['content']['parts'][0]['text']
        if "NO" in res.upper(): return None
        return json.loads(res)
    except: return None

def scrape_telegram_channel(user):
    """استخراج محتوا با پیمایش معکوس و فیلتر ۲۴ ساعته"""
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
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
                if now_utc - dt > timedelta(hours=24): continue
                
                pid = m.get("data-post")
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
                
                items.append({"text": body, "media": media, "type": m_type, "id": pid})
            except: continue
    except: pass
    return items

def main_osint_engine():
    """هسته اصلی موتور پایش"""
    if not sync_lock.acquire(blocking=False): return
    try:
        logger.info("🎬 [V53 START] - Scanning all nodes...")
        conn = db_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS seen_v48 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
                conn.commit()
        finally: db_pool.putconn(conn)

        for p_id, config in PROVINCES.items():
            for src in config['tg']:
                posts = scrape_telegram_channel(src)
                for p in posts:
                    # تولید هش محتوایی
                    clean_content = "".join(re.sub(r'[^\w]', '', p['text'][:60]).split())
                    h = hashlib.md5(clean_content.encode('utf-8')).hexdigest()
                    
                    if is_seen(h): continue
                    
                    # فراخوانی هوش مصنوعی
                    ai_res = ai_analyze_content(p['text'], config['name'])
                    if not ai_res:
                        mark_seen(h); continue
                    
                    source_url = f"https://t.me/{p['id']}"
                    safe_cap = f"<b>{ai_res.get('category','۱۱. عمومی')}</b>\n📌 <b>{ai_res.get('title','گزارش')}</b>\n\n{p['text'][:850]}\n\n🔗 <a href='{source_url}'>منبع</a>"
                    
                    sent = False
                    tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    
                    if p['media']:
                        try:
                            with requests.get(p['media'], stream=True, timeout=30) as r_stream:
                                bio = io.BytesIO()
                                for chunk in r_stream.iter_content(chunk_size=16384): bio.write(chunk)
                                bio.seek(0)
                                method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                                bio.name = "file.mp4" if p['type'] == "video" else "file.jpg"
                                r = requests.post(tg_api + method, data={"chat_id": config['channel'], "caption": safe_cap, "parse_mode": "HTML"}, files={p['type']: bio}, timeout=45)
                                sent = (r.status_code == 200)
                        except: pass
                    
                    if not sent:
                        r = requests.post(tg_api + "sendMessage", json={"chat_id": config['channel'], "text": safe_cap, "parse_mode": "HTML"}, timeout=15)
                        sent = (r.status_code == 200)
                    
                    if sent: 
                        mark_seen(h)
                        logger.info(f"✅ Dispatched: {p['id']}")
                    time.sleep(2)
    finally:
        sync_lock.release()
        logger.info("🏁 [V53 FINISHED]")

@app.route('/check')
@app.route('/')
def check_route():
    threading.Thread(target=main_osint_engine).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
