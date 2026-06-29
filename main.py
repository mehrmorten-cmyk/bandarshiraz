import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from psycopg2 import pool
from bs4 import BeautifulSoup
from flask import Flask
from datetime import datetime, timedelta, timezone

# تنظیمات لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("OSINT_V58")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_CATEGORIES = ["۱.اعتراضات", "۲.امنیتی", "۳.خدمات شهری", "۴.معیشت", "۵.سلامت", "۶.هواشناسی", "۷.مدارس", "۸.استخدام", "۹.نیازمندی", "۱۰.گمشده", "۱۱.فرهنگی"]

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
sync_lock = threading.Lock()

try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL, sslmode='require', connect_timeout=15)
except Exception as e:
    logger.critical(f"❌ DB Pool Error: {e}")

def get_hash(text):
    clean = "".join(re.sub(r'[^\w]', '', text).split())
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def ai_curator(text, province):
    if not GEMINI_API_KEY: return None
    prompt = f"سردبیر {province} باش. اگر خبر مربوط نیست فقط NO برگردان. وگرنه خروجی JSON: {{\"category\": \"...\", \"title\": \"...\"}}. لیست: {','.join(HUB_CATEGORIES)}. متن: {text[:500]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=15)
        res_text = r.json()['candidates'][0]['content']['parts'][0]['text']
        logger.info(f"🤖 AI Response: {res_text.strip()}") # لاگ حیاتی برای دیدن نظر هوش مصنوعی
        if "NO" in res_text.upper(): return None
        return json.loads(res_text)
    except Exception as e:
        logger.error(f"AI Gemini Error: {e}")
        return None

def scrape_tg(username):
    posts = []
    try:
        url = f"https://t.me/s/{username}"
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200: return posts
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
                
                txt_div = m.find("div", class_="tgme_widget_message_text")
                body = txt_div.get_text(separator="\n").strip() if txt_div else ""
                if not body: continue
                
                media, m_type = None, "text"
                v = m.find('video')
                if v: media, m_type = v.get('src'), "video"
                else:
                    ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                    if ph:
                        style = ph.get('style', '')
                        match = re.search(r"url\('([^']+)'\)", style)
                        if match: media, m_type = match.group(1), "photo"
                
                posts.append({"text": body, "media": media, "type": m_type, "id": m.get("data-post")})
            except: continue
    except: pass
    return posts

def run_v58_engine():
    if not sync_lock.acquire(blocking=False): return
    try:
        logger.info("🎬 --- ENGINE START V58 ---")
        # پاکسازی و آماده‌سازی دیتابیس (استفاده از جدول جدید برای شروع تازه)
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS seen_v58 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
            conn.commit()
        db_pool.putconn(conn)

        for p_id, config in PROVINCES.items():
            logger.info(f"🔎 Scanning: {config['name']}")
            # ارسال یک پیام تست به کانال برای اطمینان از اتصال (اختیاری - می‌توانید حذف کنید)
            # requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": config['channel'], "text": "🔄 در حال بررسی منابع جدید..."})

            for src in config['tg']:
                all_found = scrape_tg(src)
                for p in all_found:
                    h = get_hash(p['text'])
                    conn = db_pool.getconn()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1 FROM seen_v58 WHERE hash = %s", (h,))
                            if cur.fetchone():
                                db_pool.putconn(conn); continue
                        
                        logger.info(f"📝 Analyzing: {p['id']}")
                        ai_res = ai_curator(p['text'], config['name'])
                        
                        if not ai_res:
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO seen_v58 VALUES (%s) ON CONFLICT DO NOTHING", (h,))
                                conn.commit()
                            db_pool.putconn(conn); continue
                        
                        # ارسال خبر
                        logger.info(f"🚀 SENDING MATCH: {ai_res.get('title')}")
                        source_url = f"https://t.me/{p['id']}"
                        cap = f"<b>{ai_res.get('category')}</b>\n📌 <b>{ai_res.get('title')}</b>\n\n{p['text'][:850]}\n\n🔗 <a href='{source_url}'>منبع</a>"
                        
                        tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        sent = False
                        if p['media']:
                            try:
                                with requests.get(p['media'], stream=True, timeout=20) as r_stream:
                                    bio = io.BytesIO()
                                    for chunk in r_stream.iter_content(chunk_size=16384): bio.write(chunk)
                                    bio.seek(0)
                                    method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                                    r = requests.post(tg_api + method, data={"chat_id": config['channel'], "caption": cap, "parse_mode": "HTML"}, files={p['type']: ("file", bio)}, timeout=45)
                                    sent = (r.status_code == 200)
                            except: pass
                        
                        if not sent:
                            requests.post(tg_api + "sendMessage", json={"chat_id": config['channel'], "text": cap, "parse_mode": "HTML"}, timeout=15)
                        
                        with conn.cursor() as cur:
                            cur.execute("INSERT INTO seen_v58 (hash) VALUES (%s) ON CONFLICT DO NOTHING", (h,))
                            conn.commit()
                        db_pool.putconn(conn)
                        time.sleep(2)
                    except Exception as e:
                        logger.error(f"Post error: {e}")
                        db_pool.putconn(conn)
    finally:
        sync_lock.release()
        logger.info("🏁 --- ENGINE FINISHED ---")

@app.route('/')
@app.route('/check')
def manual_trigger():
    threading.Thread(target=run_v58_engine).start()
    return "V58 ACTIVE", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
