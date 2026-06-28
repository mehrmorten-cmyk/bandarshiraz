import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# دسته‌بندی ۱۱ گانه مرجع
UNIFIED_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات مردمی", "۲. ⚖️ حقوق بشر و امنیتی",
    "۳. 🚧 خدمات شهری و قطعی‌ها", "۴. 💰 معیشت و بازار",
    "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام",
    "۹. 🗝 نیازمندی‌ها و دیوار", "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

# منابع اختصاصی شما (شیراز و هرمزگان)
PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "sums1401", "shiraztopnews", "FouriFars", "FarsFouri", "avaye_shiraz", "ostan", "shiraz_news24", "shirazu1", "SaberinFars", "LineFars", "shiraz_salam", "Azad_shiraz", "Shiraz_us", "Fars_today", "eghtesadefars", "dorhamishiraziha", "Shiraz_Fouri"],
        "insta_x": ["shirazcute", "shiraztagram", "shiraz.us", "fars.online", "akhbarefars", "shiraz1400.ir", "_kakoshirazi_", "shirazlover", "farskhabar", "shiraz_eterazi"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall", "bnd_job"],
        "insta_x": ["bndonline", "hormozgan.shat", "bandarabbas.ir"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """ترمیم قطعی دیتابیس - حذف و ساخت مجدد برای رفع خطای ستون ها"""
    conn = get_db(); cur = conn.cursor()
    # ساخت جدول لاگ با تمام ستون های مورد نیاز
    cur.execute("""CREATE TABLE IF NOT EXISTS msg_logs (
        hash TEXT PRIMARY KEY, 
        channel_id TEXT, 
        msg_id TEXT, 
        title TEXT, 
        prov TEXT, 
        type TEXT,
        ts TIMESTAMP DEFAULT NOW())""")
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    
    # اطمینان از وجود ستون title (در صورتیکه جدول از قبل بود)
    try: cur.execute("ALTER TABLE msg_logs ADD COLUMN title TEXT"); conn.commit()
    except: conn.rollback()
    
    conn.commit(); cur.close(); conn.close()

def ai_handler(text, province, mode="classify"):
    if not GEMINI_API_KEY: return "۹. اخبار شهرستان‌ها"
    if mode == "classify":
        prompt = f"متن زیر را فقط در یکی از این دسته‌ها قرار بده و فقط نام دسته را بگو:\n{', '.join(UNIFIED_CATEGORIES)}\n\nمتن: {text[:500]}"
    else:
        prompt = f"این خبر را طبق پروتکل مقاومت (واژگان: رژیم، قیام، کانون‌های شورشی، خامنه‌ای جلاد) بازنویسی کن. فقط متن نهایی:\n{text}"
    
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return None

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        soup = BeautifulSoup(requests.get(url, timeout=20).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap", limit=20)
        for w in reversed(msgs):
            m = w.find("div", class_="tgme_widget_message")
            if not m: continue
            pid = m.get("data-post")
            post = {"text": "", "media": None, "type": "text", "id": pid}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            
            video = m.find('video')
            if video: post["media"] = video.get('src'); post["type"] = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    style = photo.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", style)
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: items.append(post)
    except: pass
    return items

def scrape_social(username):
    """رصد اینستاگرام و ایکس از طریق موتور جستجو"""
    items = []
    try:
        query = f'"{username}" when:1d'
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=fa&gl=IR&ceid=IR:fa"
        root = ElementTree.fromstring(requests.get(url, timeout=15).content)
        for i in root.findall(".//item")[:5]:
            items.append({"text": i.findtext("title"), "id": i.findtext("link"), "type": "text", "media": None})
    except: pass
    return items

def run_sync():
    init_db()
    for p_id, config in PROVINCES.items():
        logging.info(f"--- 📡 SYNCING {p_id.upper()} ---")
        pool = []
        for user in config['tg']: pool.extend(scrape_tg(user))
        for user in config['insta_x']: pool.extend(scrape_social(user))

        for p in pool:
            h = hashlib.md5(p['id'].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                cat = ai_handler(p['text'], config['name'], "classify")
                if cat and "NO" not in cat.upper():
                    cap = f"<b>{cat}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='{p['id']}'>منبع اصلی</a>"
                    kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    try:
                        res = None
                        if p['type'] == "video" and p['media']:
                            video_data = requests.get(p['media']).content
                            res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", video_data)})
                        elif p['type'] == "photo" and p['media']:
                            res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML", "reply_markup":kb})
                        else:
                            res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML", "reply_markup":kb})
                        
                        if res and res.status_code == 200:
                            m_id = res.json()['result']['message_id']
                            cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                            cur.execute("INSERT INTO msg_logs (hash, channel_id, msg_id, title, prov, type) VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:1000], p_id, p['type']))
                            conn.commit()
                    except: pass
            cur.close(); conn.close()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True)
    if not data or "callback_query" not in data: return "OK"
    cb = data["callback_query"]; h = cb["data"][3:]
    
    # متوقف کردن لودینگ دکمه در تلگرام
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"], "text": "⏳ در حال بازنویسی..."})
    
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT title, channel_id, msg_id, prov, type FROM msg_logs WHERE hash = %s", (h,))
    row = cur.fetchone()
    if row:
        title, c_id, m_id, prov, m_type = row
        new_txt = ai_handler(title, prov, "rewrite")
        if new_txt:
            method = "editMessageCaption" if m_type != "text" else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", 
                         json={"chat_id": c_id, "message_id": int(m_id), "caption" if m_type != "text" else "text": f"✊ <b>نسخه مقاومت</b>\n\n{new_txt}", "parse_mode": "HTML"})
    cur.close(); conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_sync).start()
    return "Syncing with all sources..."

@app.route('/')
def home(): return "Reference Hub v26 Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
