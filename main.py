import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io, random
from psycopg2 import pool
from bs4 import BeautifulSoup
from flask import Flask
from datetime import datetime, timedelta, timezone

# پیکربندی لاگ حرفه‌ای و یکپارچه
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("OSINT_V60_FINAL")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_CATEGORIES = ["۱.اعتراضات", "۲.امنیتی", "۳.خدمات شهری", "۴.معیشت", "۵.سلامت", "۶.هواشناسی", "۷.مدارس", "۸.استخدام", "۹.نیازمندی", "۱۰.گمشده", "۱۱.فرهنگی"]

PROVINCES = {
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", 
        "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today"]
    },
    "fars": {
        "name": "فارس و شیراز", 
        "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "shiraz_online", "Shiraz_Fouri"]
    }
}

# لیست مرورگرهای واقعی برای عبور از سد فیلترینگ تلگرام وب
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

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
    if not GEMINI_API_KEY: 
        logger.warning("⚠️ GEMINI_API_KEY یافت نشد.")
        return None
    prompt = f"سردبیر {province} باش. متن را تحلیل کن. اگر مربوط نیست NO. وگرنه خروجی JSON: {{\"category\": \"...\", \"title\": \"...\"}}. لیست: {','.join(HUB_CATEGORIES)}. متن: {text[:500]}"
    
    # استفاده از نسخه پایدار v1 برای پایداری ۱۰۰٪ مدل
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    try:
        time.sleep(4)  # رعایت محدودیت نرخ درخواست گوگل
        r = requests.post(url, json=payload, timeout=20)
        data = r.json()
        
        if 'candidates' in data:
            res_text = data['candidates'][0]['content']['parts'][0]['text']
            if "NO" in res_text.upper(): return None
            return json.loads(res_text)
        else:
            logger.error(f"⚠️ Gemini API Error: {json.dumps(data)}")
            return None
    except Exception as e:
        logger.error(f"❌ Gemini Request Failed: {e}")
        return None

def scrape_tg(username):
    posts = []
    try:
        url = f"https://t.me/s/{username}"
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }
        resp = requests.get(url, timeout=20, headers=headers)
        if resp.status_code != 200:
            logger.warning(f"⚠️ Telegram returned status {resp.status_code} for @{username}")
            return posts
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        
        if not msgs:
            logger.warning(f"⚠️ No html messages found for @{username}")
            
        now_utc = datetime.now(timezone.utc)
        for w in msgs:
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
                        st = ph.get('style', '')
                        match = re.search(r"url\('([^']+)'\)", st)
                        if match: media, m_type = match.group(1), "photo"
                        
                posts.append({"text": body, "media": media, "type": m_type, "id": m.get("data-post")})
            except: continue
    except Exception as e:
        logger.error(f"❌ Scraper error for @{username}: {e}")
    return posts

def run_v60_engine():
    if not sync_lock.acquire(blocking=False): return
    try:
        logger.info("🎬 --- OSINT ENGINE START V60 (PROSTABLE) ---")
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS seen_v60 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
            conn.commit()
        db_pool.putconn(conn)

        for p_id, config in PROVINCES.items():
            for src in config['tg']:
                all_found = scrape_tg(src)
                time.sleep(2)  # وقفه کوتاه برای حفظ پایداری و عدم حساسیت تلگرام
                
                for p in all_found:
                    h = get_hash(p['text'])
                    conn = db_pool.getconn()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1 FROM seen_v60 WHERE hash = %s", (h,))
                            if cur.fetchone():
                                db_pool.putconn(conn); continue
                        
                        logger.info(f"📝 Analyzing: {p['id']} from @{src}")
                        ai_res = ai_curator(p['text'], config['name'])
                        
                        # چه خبر تایید شود و چه رد، هش آن ذخیره می‌شود تا سهمیه هدر نرود
                        with conn.cursor() as cur:
                            cur.execute("INSERT INTO seen_v60 (hash) VALUES (%s) ON CONFLICT DO NOTHING", (h,))
                            conn.commit()
                        
                        if not ai_res:
                            db_pool.putconn(conn); continue
                        
                        # ساختار کپشن تلگرام و ارسال به کانال اختصاصی همان استان
                        cap = f"<b>{ai_res.get('category')}</b>\n📌 <b>{ai_res.get('title')}</b>\n\n{p['text'][:850]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع</a>"
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        sent = False
                        
                        if p['media']:
                            try:
                                with requests.get(p['media'], stream=True, timeout=20) as r_stream:
                                    bio = io.BytesIO()
                                    for chunk in r_stream.iter_content(chunk_size=16384): bio.write(chunk)
                                    bio.seek(0)
                                    method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                                    r = requests.post(tg_url + method, data={"chat_id": config['channel'], "caption": cap, "parse_mode": "HTML"}, files={p['type']: ("file", bio)}, timeout=45)
                                    sent = (r.status_code == 200)
                            except: pass
                        
                        if not sent:
                            requests.post(tg_url + "sendMessage", json={"chat_id": config['channel'], "text": cap, "parse_mode": "HTML"}, timeout=15)
                        
                        db_pool.putconn(conn)
                        logger.info(f"✅ DISPATCHED SUCCESS: {p['id']} to Channel {config['channel']}")
                    except Exception as e:
                        logger.error(f"Error in processing post: {e}")
                        db_pool.putconn(conn)
    finally:
        sync_lock.release()
        logger.info("🏁 --- ENGINE FINISHED ---")

@app.route('/')
def home():
    return "OSINT Node is Online", 200

@app.route('/check')
def check():
    threading.Thread(target=run_v60_engine).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
