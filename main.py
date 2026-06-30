import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io, random
from psycopg2 import pool
from bs4 import BeautifulSoup
from flask import Flask
import feedparser # اضافه شدن فیدپارسر برای خواندن فیدهای واسطه
from datetime import datetime, timedelta, timezone

# لاگینگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("OSINT_V61_FINAL")

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
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "shiraz_online", "Shiraz_Fouri"]
    }
}

app = Flask(__name__)
sync_lock = threading.Lock()

try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL, sslmode='require', connect_timeout=15)
except Exception as e:
    logger.critical(f"❌ DB Pool Error: {e}")

def get_hash(text):
    if not text: return "empty"
    clean = "".join(re.sub(r'[^\w]', '', text).split())
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def ai_curator(text, province):
    if not GEMINI_API_KEY: return None
    prompt = f"سردبیر {province} باش. اگر خبر مربوط نیست فقط NO برگردان. وگرنه خروجی JSON: {{\"category\": \"...\", \"title\": \"...\"}}. لیست: {','.join(HUB_CATEGORIES)}. متن: {text[:500]}"
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}, {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    try:
        time.sleep(3) # جلوگیری از بلاک شدن توسط گوگل
        r = requests.post(url, json=payload, timeout=20)
        data = r.json()
        if 'candidates' in data:
            res_text = data['candidates'][0]['content']['parts'][0]['text']
            if "NO" in res_text.upper(): return None
            return json.loads(res_text)
        return None
    except: return None

def scrape_tg_via_bridge(username):
    """استفاده از پل واسطه RSS برای دور زدن مسدودی آی‌پی رندر"""
    posts = []
    # لیست سرورهای واسطه (اگر یکی مسدود بود سراغ بعدی می‌رود)
    bridges = [
        f"https://rsshub.app/telegram/channel/{username}",
        f"https://rss.artemislena.eu.org/telegram/channel/{username}",
        f"https://rsshub.rss.rocks/telegram/channel/{username}"
    ]
    
    for url in bridges:
        try:
            logger.info(f"📡 Requesting Bridge for @{username} via {url[:25]}")
            feed = feedparser.parse(url)
            if not feed.entries: continue
            
            now_utc = datetime.now(timezone.utc)
            for entry in feed.entries[:10]:
                # فیلتر ۲۴ ساعته
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if now_utc - pub_date > timedelta(hours=24): continue
                
                soup = BeautifulSoup(entry.description, 'html.parser')
                text = soup.get_text(separator="\n").strip()
                
                # استخراج عکس از توضیحات فید
                media = None
                img_tag = soup.find('img')
                if img_tag: media = img_tag.get('src')
                
                posts.append({
                    "text": text,
                    "media": media,
                    "type": "photo" if media else "text",
                    "id": entry.link.split('/')[-1] # استخراج آیدی پست
                })
            
            if posts: break # اگر محتوا پیدا شد، دیگر سراغ بریج‌های بعدی نرو
        except: continue
    return posts

def run_v61_engine():
    if not sync_lock.acquire(blocking=False): return
    try:
        logger.info("🎬 --- OSINT ENGINE START V61 (ANTI-BLOCK) ---")
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS seen_v61 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
            conn.commit()
        db_pool.putconn(conn)

        for p_id, config in PROVINCES.items():
            for src in config['tg']:
                all_found = scrape_tg_via_bridge(src)
                for p in all_found:
                    h = get_hash(p['text'])
                    conn = db_pool.getconn()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1 FROM seen_v61 WHERE hash = %s", (h,))
                            if cur.fetchone():
                                db_pool.putconn(conn); continue
                        
                        logger.info(f"📝 Analyzing: {p['id']} from @{src}")
                        ai_res = ai_curator(p['text'], config['name'])
                        
                        # ذخیره هش برای جلوگیری از تکرار
                        with conn.cursor() as cur:
                            cur.execute("INSERT INTO seen_v61 VALUES (%s) ON CONFLICT DO NOTHING", (h,))
                            conn.commit()
                        
                        if not ai_res:
                            db_pool.putconn(conn); continue
                        
                        source_url = f"https://t.me/{src}/{p['id']}"
                        cap = f"<b>{ai_res.get('category')}</b>\n📌 <b>{ai_res.get('title')}</b>\n\n{p['text'][:850]}\n\n🔗 <a href='{source_url}'>منبع</a>"
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        
                        sent = False
                        if p['media']:
                            try:
                                with requests.get(p['media'], stream=True, timeout=20) as r_stream:
                                    bio = io.BytesIO()
                                    for chunk in r_stream.iter_content(chunk_size=16384): bio.write(chunk)
                                    bio.seek(0)
                                    r = requests.post(tg_url + "sendPhoto", data={"chat_id": config['channel'], "caption": cap, "parse_mode": "HTML"}, files={"photo": ("file.jpg", bio)}, timeout=40)
                                    sent = (r.status_code == 200)
                            except: pass
                        
                        if not sent:
                            requests.post(tg_url + "sendMessage", json={"chat_id": config['channel'], "text": cap, "parse_mode": "HTML"}, timeout=15)
                        
                        db_pool.putconn(conn)
                        logger.info(f"✅ SUCCESS: {p['id']} sent to {config['name']}")
                        time.sleep(2)
                    except Exception as e:
                        logger.error(f"Error: {e}")
                        db_pool.putconn(conn)
    finally:
        sync_lock.release()
        logger.info("🏁 --- ENGINE FINISHED ---")

@app.route('/')
@app.route('/check')
def check():
    threading.Thread(target=run_v61_engine).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
