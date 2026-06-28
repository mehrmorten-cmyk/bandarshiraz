import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

UNIFIED_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات مردمی", "۲. ⚖️ حقوق بشر و امنیتی",
    "۳. 🚧 خدمات شهری و قطعی‌ها", "۴. 💰 معیشت و بازار",
    "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام",
    "۹. 🗝 نیازمندی‌ها و دیوار", "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "sums1401", "shiraztopnews", "FouriFars", "FarsFouri", "avaye_shiraz", "shiraz_news24", "Shiraz_Fouri", "shiraz_salam"],
        "insta": ["shirazcute", "shiraztagram", "shiraz.us", "fars.online", "akhbarefars", "shiraz1400.ir", "_kakoshirazi_", "shirazlover", "farskhabar"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall"],
        "insta": ["bndonline", "hormozgan.shat", "bandarabbas.ir"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """ایجاد جداول جدید برای اطمینان از وجود تمام ستون‌ها"""
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    # استفاده از جدول نسخه ۲ برای حل قطعی خطای UndefinedColumn
    cur.execute("""CREATE TABLE IF NOT EXISTS msg_logs_v2 (
        hash TEXT PRIMARY KEY, 
        channel_id TEXT, 
        msg_id TEXT, 
        title TEXT, 
        prov TEXT, 
        type TEXT,
        ts TIMESTAMP DEFAULT NOW())""")
    conn.commit(); cur.close(); conn.close()

def ai_call(text, province, mode="classify"):
    if not GEMINI_API_KEY: return None
    prompt = f"دسته خبر را بگو: {', '.join(UNIFIED_CATEGORIES)}\nمتن: {text[:400]}" if mode=="classify" else f"این خبر از {province} را با واژگان انقلابی بازنویسی کن:\n{text}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return None

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        soup = BeautifulSoup(requests.get(url, timeout=15).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap", limit=20)
        for w in msgs:
            m = w.find("div", class_="tgme_widget_message")
            if not m or not m.get("data-post"): continue
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            video = m.find('video')
            if video: post["media"] = video.get('src'); post["type"] = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    match = re.search(r"url\('([^']+)'\)", photo.get('style', ''))
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: items.append(post)
    except: pass
    return items

def run_sync():
    init_db()
    for p_id, config in PROVINCES.items():
        pool = []
        for user in config['tg']: pool.extend(scrape_tg(user))
        for p in pool:
            h = hashlib.md5(p['id'].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                cat = ai_call(p['text'], config['name'], "classify")
                if cat and "NO" not in cat.upper():
                    cap = f"<b>{cat}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع</a>"
                    kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                    try:
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        if p['type'] == "video" and p['media']:
                            r = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", requests.get(p['media']).content)})
                        elif p['type'] == "photo" and p['media']:
                            r = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML", "reply_markup":kb})
                        else:
                            r = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML", "reply_markup":kb})
                        
                        if r.status_code == 200:
                            m_id = r.json()['result']['message_id']
                            cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                            cur.execute("INSERT INTO msg_logs_v2 VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:1000], p_id, p['type']))
                            conn.commit()
                    except: pass
            cur.close(); conn.close()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True)
    if not data or "callback_query" not in data: return "OK"
    cb = data["callback_query"]; h = cb["data"][3:]
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"], "text": "⏳ در حال بازنویسی..."})
    
    conn = get_db(); cur = conn.cursor()
    # استفاده از جدول جدید v2
    cur.execute("SELECT title, channel_id, msg_id, prov, type FROM msg_logs_v2 WHERE hash = %s", (h,))
    row = cur.fetchone()
    if row:
        new_txt = ai_call(row[0], row[3], "rewrite")
        if new_txt:
            method = "editMessageCaption" if row[4] != "text" else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", 
                         json={"chat_id": row[1], "message_id": int(row[2]), "caption" if row[4] != "text" else "text": f"✊ <b>نسخه مقاومت</b>\n\n{new_txt}", "parse_mode": "HTML"})
    cur.close(); conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_sync).start()
    return "Syncing..."

@app.route('/')
def home(): return "Bot Online v27"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
