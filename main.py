import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree
from urllib.parse import quote

# پیکربندی لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HUB_OSINT")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

UNIFIED_CATEGORIES = [
    "۱. 🚨 اعتراضات، تجمعات و مطالبات مردمی",
    "۲. ⚖️ حقوق بشر، بازداشت‌ها و رویدادهای امنیتی",
    "۳. 🚧 خدمات شهری، زیرساخت و قطعی‌ها",
    "۴. 💰 معیشت، بازار و کالاهای اساسی",
    "۵. 🏥 دارو، درمان و سلامت جامعه",
    "۶. 🌦 هواشناسی، جاده‌ها و محیط زیست",
    "۷. 🎓 مدارس، دانشگاه‌ها و رویدادهای علمی",
    "۸. 💼 استخدام و فرصت‌های شغلی",
    "۹. 🗝 نیازمندی‌ها، آگهی و دیوار استانی",
    "۱۰. 🔍 گم‌شده‌ها و پیداشده‌ها",
    "۱۱. 🎭 فرهنگی، گردشگری و ورزش"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "sources": ["shiraz_online", "akhbarshiraz", "asrshiraz", "shiraz_ma", "shiraz_neiaz", "divar_shiraz"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "sources": ["hormozgan_online", "bndonline", "bandar_news", "hormozgan_today", "bnd_wall"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT, type TEXT)")
    conn.commit(); cur.close(); conn.close()

def ai_process(text, province, mode="classify"):
    """پردازش دوگانه: دسته‌بندی و بازنویسی مقاومت"""
    if not GEMINI_API_KEY: return "سایر"
    
    if mode == "classify":
        prompt = f"متن زیر را در یکی از این ۱۱ دسته قرار بده و فقط نام دسته را بگو. اگر مربوط به {province} نیست بگو NO.\nدسته‌ها: {', '.join(UNIFIED_CATEGORIES)}\n\nمتن: {text[:500]}"
    else:
        prompt = f"این خبر را طبق پروتکل مقاومت و واژگان انقلابی بازنویسی کن. فقط متن نهایی را بده:\n{text[:1000]}"

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return None

def scrape_tg(tg_user):
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        resp = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap", limit=30) # افزایش عمق به ۳۰ پیام
        for w in reversed(msgs):
            m = w.find("div", class_="tgme_widget_message")
            if not m: continue
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            
            video = m.find('video')
            if video: 
                post["media"] = video.get('src'); post["type"] = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    style = photo.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", style)
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: items.append(post)
    except Exception as e: logger.error(f"Scrape Error @{tg_user}: {e}")
    return items

def run_pro_cycle():
    init_db()
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 STARTING {p_id.upper()} ---")
        for src in config['sources']:
            try:
                posts = scrape_tg(src)
                logger.info(f"Source @{src}: Found {len(posts)} posts")
                for p in posts:
                    h = hashlib.md5(p['id'].encode()).hexdigest()
                    conn = get_db(); cur = conn.cursor()
                    cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
                    if not cur.fetchone():
                        cat = ai_process(p['text'], config['name'], "classify")
                        if cat and "NO" not in cat.upper():
                            logger.info(f"New Match: {p['id']}")
                            caption = f"<b>{cat}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                            kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                            
                            tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                            res = None
                            if p['type'] == "video" and p['media']:
                                video_data = requests.get(p['media']).content
                                res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":caption, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", video_data)})
                            elif p['type'] == "photo" and p['media']:
                                res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":caption, "parse_mode":"HTML", "reply_markup":kb})
                            else:
                                res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":caption, "parse_mode":"HTML", "reply_markup":kb})
                            
                            if res and res.status_code == 200:
                                m_id = res.json()['result']['message_id']
                                cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                                cur.execute("INSERT INTO msg_logs (hash, channel_id, msg_id, title, prov, type) VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:400], p_id, p['type']))
                                conn.commit()
                    cur.close(); conn.close()
                    time.sleep(1) # جلوگیری از بلاک شدن
            except Exception as e:
                logger.error(f"Error in source {src}: {e}")
                continue

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "callback_query" in data:
        cb = data["callback_query"]; h = cb["data"][3:]
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT title, channel_id, msg_id, prov, type FROM msg_logs WHERE hash = %s", (h,))
        row = cur.fetchone()
        if row:
            title, c_id, m_id, prov, m_type = row
            # پاسخ فوری به تلگرام برای متوقف کردن چرخش دکمه
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"], "text": "⏳ در حال بازنویسی..."})
            
            # بازنویسی با Gemini
            new_txt = ai_process(title, prov, "rewrite")
            if new_txt:
                method = "editMessageCaption" if m_type != "text" else "editMessageText"
                final_caption = f"✊ <b>نسخه مقاومت ({prov})</b>\n\n{new_txt.strip()}"
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", 
                             json={"chat_id": c_id, "message_id": int(m_id), "caption" if m_type != "text" else "text": final_caption, "parse_mode": "HTML"})
        cur.close(); conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_pro_cycle).start()
    return "OSINT Reference Hub is Syncing across ALL sources..."

@app.route('/')
def home(): return "Province Hub v23 Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
