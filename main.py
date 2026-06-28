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
    "fars": {"name": "فارس و شیراز", "channel": "-1004352884396", "sources": ["shiraz_online", "akhbarshiraz", "asrshiraz", "shiraz_ma"]},
    "hormozgan": {"name": "هرمزگان و بندرعباس", "channel": "-1003915149928", "sources": ["hormozgan_online", "bndonline", "bandar_news", "hormozgan_today"]}
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """اصلاح اجباری ستون‌های گمشده دیتابیس"""
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT, type TEXT)")
    # ترمیم جدول msg_logs برای ستون‌های احتمالی گمشده
    cols = ['title', 'prov', 'type', 'channel_id', 'msg_id']
    for col in cols:
        try: cur.execute(f"ALTER TABLE msg_logs ADD COLUMN {col} TEXT"); conn.commit()
        except: conn.rollback()
    cur.close(); conn.close()

def ai_call(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return None

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        soup = BeautifulSoup(requests.get(url, timeout=20).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap", limit=25)
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

def run_sync():
    init_db()
    for p_id, config in PROVINCES.items():
        for src in config['sources']:
            pool = scrape_tg(src)
            for p in pool:
                h = hashlib.md5(p['id'].encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
                if not cur.fetchone():
                    cat = ai_call(f"از این لیست یک دسته انتخاب کن: {', '.join(UNIFIED_CATEGORIES)}\nمتن: {p['text'][:300]}")
                    if cat:
                        cap = f"<b>{cat}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                        kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        try:
                            res = None
                            if p['type'] == "video" and p['media']:
                                v_data = requests.get(p['media']).content
                                res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", v_data)})
                            elif p['type'] == "photo" and p['media']:
                                res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML", "reply_markup":kb})
                            else:
                                res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML", "reply_markup":kb})

                            if res and res.status_code == 200:
                                m_id = res.json()['result']['message_id']
                                cur.execute("INSERT INTO seen_news (hash) VALUES (%s)", (h,))
                                cur.execute("INSERT INTO msg_logs (hash, channel_id, msg_id, title, prov, type) VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:800], p_id, p['type']))
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
    cur.execute("SELECT title, channel_id, msg_id, prov, type FROM msg_logs WHERE hash = %s", (h,))
    row = cur.fetchone()
    if row:
        title, c_id, m_id, prov, m_type = row
        new_txt = ai_call(f"این خبر را طبق پروتکل مقاومت بازنویسی کن:\n{title}")
        if new_txt:
            method = "editMessageCaption" if m_type != "text" else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", 
                         json={"chat_id": c_id, "message_id": int(m_id), "caption" if m_type != "text" else "text": f"✊ <b>نسخه مقاومت ({prov})</b>\n\n{new_txt}", "parse_mode": "HTML"})
    cur.close(); conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_sync).start()
    return "Syncing..."

@app.route('/')
def home(): return "Bot Online v25"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
