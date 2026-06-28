import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree
from urllib.parse import quote

# Logging Setup
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("PRO_OSINT")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

UNIFIED_CATEGORIES = [
    "۱. 🚨 اعتراضات، تجمعات و مطالبات مردمی", "۲. ⚖️ حقوق بشر و امنیتی",
    "۳. 🚧 خدمات شهری و قطعی‌ها", "۴. 💰 معیشت و بازار",
    "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام",
    "۹. 🗝 نیازمندی‌ها و دیوار", "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "keys": ["شیراز", "استان فارس", "مرودشت", "کازرون", "فسا"],
        "sources": ["shiraz_online", "akhbarshiraz", "asrshiraz", "shiraz_ma", "shiraz_neiaz", "divar_shiraz"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "keys": ["بندرعباس", "هرمزگان", "قشم", "کیش", "میناب"],
        "sources": ["hormozgan_online", "bndonline", "bandar_news", "hormozgan_today", "bnd_wall"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def ai_call(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return None

def scrape_tg_web(user):
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

def global_search(query):
    """جستجوی سراسری در کل تلگرام از طریق گوگل"""
    items = []
    try:
        search_url = f"https://news.google.com/rss/search?q={quote(query)}+site:t.me+when:1d&hl=fa&gl=IR&ceid=IR:fa"
        root = ElementTree.fromstring(requests.get(search_url, timeout=15).content)
        for i in root.findall(".//item")[:10]:
            link = i.findtext("link")
            if "/s/" not in link and "t.me/" in link:
                user = link.split("/")[-2] if link.endswith("/") else link.split("/")[-1]
                items.append({"text": i.findtext("title"), "id": link, "type": "text", "media": None})
    except: pass
    return items

def run_sync():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT, type TEXT)")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SYNCING {config['name']} ---")
        pool = []
        # ۱. کانال‌های هدف
        for src in config['sources']: pool.extend(scrape_tg_web(src))
        # ۲. جستجوی سراسری تلگرام
        for key in config['keys']: pool.extend(global_search(key))

        for p in pool:
            h = hashlib.md5(p['id'].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                cat = ai_call(f"فقط نام یکی از این دسته‌ها را برای متن زیر بگو. اگر مربوط به {config['name']} نیست بگو NO: {', '.join(UNIFIED_CATEGORIES)}\n\nمتن: {p['text'][:400]}")
                if cat and "NO" not in cat.upper():
                    logger.info(f"Found: {p['id']}")
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
                            cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:300], p_id, p['type']))
                            conn.commit()
                    except: pass
            cur.close(); conn.close()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True)
    if not data or "callback_query" not in data:
        return "OK" # جلوگیری از کرش در صورت دیتای غیرمنتظره
    
    cb = data["callback_query"]; h = cb["data"][3:]
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"], "text": "⏳ در حال بازنویسی..."})
    
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT title, channel_id, msg_id, prov, type FROM msg_logs WHERE hash = %s", (h,))
    row = cur.fetchone()
    if row:
        new_txt = ai_call(f"این خبر را طبق پروتکل مقاومت بازنویسی کن. فقط متن نهایی:\n{row[0]}")
        if new_txt:
            method = "editMessageCaption" if row[4] != "text" else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", 
                         json={"chat_id": row[1], "message_id": int(row[2]), "caption" if row[4] != "text" else "text": f"✊ <b>نسخه مقاومت ({row[3]})</b>\n\n{new_txt}", "parse_mode": "HTML"})
    cur.close(); conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_sync).start()
    return "OSINT Synchronization Started Successfully."

@app.route('/')
def home(): return "Province OSINT Engine v24 Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
