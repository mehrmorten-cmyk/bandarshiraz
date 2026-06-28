import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OSINT_PRO")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "tg_sources": ["shiraz_online", "akhbarshiraz", "asrshiraz", "shiraz_ma"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "tg_sources": ["hormozgan_online", "bndonline", "akhbar_hormozgan", "hormozgan_today"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT, type TEXT)")
    conn.commit(); cur.close(); conn.close()

def ai_classify_gate(text, province):
    """دروازه‌بان هوشمند: تفکیک دقیق ۱۱ موضوع و حذف موارد کاملاً بی‌ربط"""
    if not GEMINI_API_KEY: return "سایر اخبار"
    prompt = f"تو سردبیر اخبار استان {province} هستی. اگر این متن مربوط به این استان است، فقط نام یکی از این ۱۱ دسته را بگو، وگرنه بگو NO:\n۱.اعتراضات، ۲.امنیتی، ۳.حقوق بشر، ۴.نان، ۵.سوخت، ۶.دارو، ۷.آب و برق، ۸.معیشت، ۹.شهرستان‌ها، ۱۰.جمع‌بندی، ۱۱.منابع\n\nمتن: {text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        res = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        return None if "NO" in res.upper() else res
    except: return "۹. اخبار شهرستان‌ها"

def scrape_tg_deep(tg_user):
    """رصد عمیق ۲۰ پست آخر کانال با متد استخراج آیدی عددی"""
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap", limit=20)
        
        for w in reversed(msgs): # بررسی از جدیدترین به قدیمی‌ترین
            m = w.find("div", class_="tgme_widget_message")
            if not m: continue
            
            p_id = m.get("data-post") # آیدی منحصربه‌فرد پست (مثلاً shiraz/123)
            if not p_id: continue

            post = {"text": "", "media": None, "type": "text", "id": p_id}
            
            txt_div = m.find("div", class_="tgme_widget_message_text")
            if txt_div: post["text"] = txt_div.get_text(separator="\n").strip()
            
            video = m.find('video')
            if video: 
                post["media"] = video.get('src'); post["type"] = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    style = photo.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", style)
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            
            if post["text"] or post["media"]: items.append(post)
    except Exception as e: logger.error(f"Scrape Error @{tg_user}: {e}")
    return items

def run_pro_check():
    init_db()
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {p_id.upper()} ---")
        for src in config['tg_sources']:
            posts = scrape_tg_deep(src)
            for p in posts:
                # استفاده از آیدی پلتفرم برای جلوگیری از تداخل
                h = hashlib.md5(p['id'].encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
                if not cur.fetchone():
                    # فیلتر هوشمند
                    category = ai_classify_gate(p['text'], config['name'])
                    if category:
                        logger.info(f"✅ NEW POST: {p['id']}")
                        caption = f"📌 <b>{category}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                        kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                        
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        try:
                            res = None
                            if p['type'] == "video" and p['media']:
                                v_data = requests.get(p['media']).content
                                res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":caption, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", v_data)})
                            elif p['type'] == "photo" and p['media']:
                                res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":caption, "parse_mode":"HTML", "reply_markup":kb})
                            else:
                                res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":caption, "parse_mode":"HTML", "reply_markup":kb})

                            if res and res.status_code == 200:
                                m_id = res.json()['result']['message_id']
                                cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                                cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:300], p_id, p['type']))
                                conn.commit()
                        except: continue
                cur.close(); conn.close()
                time.sleep(1)

@app.route('/check')
def check():
    threading.Thread(target=run_pro_check).start()
    return "Deep Scrape v20 Started. Full Telegram sync active."

@app.route('/webhook', methods=['POST'])
def webhook():
    # بخش بازنویسی (بدون تغییر)
    data = request.json
    if "callback_query" in data:
        cb = data["callback_query"]; h = cb["data"][3:]
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT title, channel_id, msg_id, prov, type FROM msg_logs WHERE hash = %s", (h,))
        row = cur.fetchone()
        if row:
            title, c_id, m_id, prov, m_type = row
            prompt = f"این خبر را طبق پروتکل مقاومت بازنویسی کن. فقط متن نهایی:\n{title}"
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", json={"contents": [{"parts": [{"text": prompt}]}]})
            new_txt = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            method = "editMessageCaption" if m_type != "text" else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json={"chat_id": c_id, "message_id": int(m_id), "caption" if m_type != "text" else "text": f"✊ <b>نسخه مقاومت</b>\n\n{new_txt}", "parse_mode": "HTML"})
        cur.close(); conn.close()
    return "OK"

@app.route('/')
def home(): return "OSINT Engine v20 Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
