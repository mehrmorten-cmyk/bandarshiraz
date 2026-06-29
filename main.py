import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from psycopg2 import pool
from bs4 import BeautifulSoup
from flask import Flask
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("FINAL_V49")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_CATEGORIES = ["۱. 🚨 اعتراضات و مطالبات", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و قطعی‌ها", "۴. 💰 معیشت و بازار", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده", "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام", "۹. 🗝 نیازمندی‌ها و دیوار", "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"]

PROVINCES = {
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall"],
        "queries": ["بندرعباس قطعی برق", "قشم اعتراض", "هرمزگان معیشت"]
    },
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "shiraz_online", "SaberinFars", "shiraz_salam", "Shiraz_Fouri"],
        "queries": ["شیراز اعتراض", "مرودشت قطعی برق", "کازرون معیشت"]
    }
}

app = Flask(__name__)
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, DATABASE_URL, sslmode='require', connect_timeout=15)
except Exception as e:
    logger.critical(f"DB Pool Error: {e}")

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

def ai_analyze(text, province):
    if not GEMINI_API_KEY: return None
    prompt = f"تحلیلگر استان {province} باش. اگر خبر مربوط نیست فقط NO برگردان. وگرنه JSON: {{\"category\": \"...\", \"title\": \"...\"}}. لیست: {','.join(HUB_CATEGORIES)}. متن: {text[:500]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=12)
        res = r.json()['candidates'][0]['content']['parts'][0]['text']
        return None if "NO" in res.upper() else json.loads(res)
    except: return None

def run_v49_engine():
    logger.info("[PROCESS START] - Checking all sources...")
    for p_id, config in PROVINCES.items():
        for src in config['tg']:
            try:
                url = f"https://t.me/s/{src}"
                soup = BeautifulSoup(requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"}).text, 'html.parser')
                msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
                now_utc = datetime.now(timezone.utc)
                for w in msgs[-15:]:
                    m = w.find("div", class_="tgme_widget_message")
                    t_tag = w.find("time")
                    if not m or not t_tag: continue
                    dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00'))
                    if now_utc - dt > timedelta(hours=24): continue
                    
                    body = m.find("div", class_="tgme_widget_message_text").get_text(separator="\n").strip() if m.find("div", class_="tgme_widget_message_text") else ""
                    if not body: continue
                    
                    h = hashlib.md5(re.sub(r'[^\w]', '', body[:60]).encode()).hexdigest()
                    if is_seen(h): continue
                    
                    ai_res = ai_analyze(body, config['name'])
                    if not ai_res:
                        mark_seen(h); continue
                    
                    source_url = f"https://t.me/{m.get('data-post')}"
                    safe_cap = f"<b>{ai_res.get('category','۱۱. عمومی')}</b>\n📌 <b>{ai_res.get('title','گزارش')}</b>\n\n{body[:850]}\n\n🔗 <a href='{source_url}'>منبع</a>"
                    
                    sent = False
                    tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    
                    # استخراج عکس/فیلم
                    media, m_type = None, "text"
                    v = m.find('video')
                    if v: media, m_type = v.get('src'), "video"
                    else:
                        ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                        if ph: 
                            match = re.search(r"url\('([^']+)'\)", ph.get('style', ''))
                            if match: media, m_type = match.group(1), "photo"
                    
                    if media:
                        try:
                            with requests.get(media, stream=True) as r_stream:
                                bio = io.BytesIO()
                                for chunk in r_stream.iter_content(chunk_size=16384): bio.write(chunk)
                                bio.seek(0)
                                method = "sendVideo" if m_type == "video" else "sendPhoto"
                                bio.name = "file.mp4" if m_type == "video" else "file.jpg"
                                requests.post(tg_api + method, data={"chat_id": config['channel'], "caption": safe_cap, "parse_mode": "HTML"}, files={m_type: bio}, timeout=40)
                                sent = True
                        except: pass
                    
                    if not sent:
                        requests.post(tg_api + "sendMessage", json={"chat_id": config['channel'], "text": safe_cap, "parse_mode": "HTML"}, timeout=15)
                    
                    mark_seen(h)
                    time.sleep(2)
            except: continue

@app.route('/check')
@app.route('/') # پچ استراتژیک: هر دو آدرس جستجو را استارت می‌زنند
def check():
    threading.Thread(target=run_v49_engine).start()
    return "ENGINE V49: ACTIVE", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
