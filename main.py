import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree

# تنظیمات لاگ (رفع خطای NameError)
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# موضوعات ۱۱‌گانه بولتن شما
CATEGORIES = [
    "۱. اعتراضات و تجمع‌های مردمی", "۲. رویدادهای امنیتی", "۳. بازداشت‌ها و پرونده‌های حقوق بشری",
    "۴. نان، آرد و کالاهای اساسی", "۵. سوخت (بنزین و گازوئیل)", "۶. دارو و خدمات درمانی",
    "۷. آب، برق و گاز", "۸. اقتصاد، بازار و معیشت", "۹. اخبار شهرستان‌ها", "۱۰. جمع‌بندی", "۱۱. منابع"
]

# منابع هوشمند
SMART_SOURCES = {
    "fars": ["shiraz_online", "akhbarshiraz", "asrshiraz"],
    "hormozgan": ["hormozgan_online", "bndonline", "akhbar_hormozgan"]
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
        cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT, type TEXT)")
        cur.execute("CREATE TABLE IF NOT EXISTS manual_sources (username TEXT PRIMARY KEY, province TEXT)")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: logging.error(f"DB Error: {e}")

def ai_classify(text):
    if not GEMINI_API_KEY: return "سایر اخبار"
    prompt = f"متن خبر را فقط در یکی از این ۱۱ دسته قرار بده و فقط نام دسته را برگردان:\n{', '.join(CATEGORIES)}\n\nخبر:\n{text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۹. اخبار شهرستان‌ها"

def scrape_tg(tg_user):
    items = []
    try:
        logging.info(f"Checking @{tg_user}...")
        url = f"https://t.me/s/{tg_user}"
        soup = BeautifulSoup(requests.get(url, timeout=15).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        for w in msgs[-5:]:
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
    except Exception as e: logging.error(f"Scrape error: {e}")
    return items

def run_check():
    init_db()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT username, province FROM manual_sources")
    manuals = cur.fetchall()
    
    prov_map = {"fars": "-1004352884396", "hormozgan": "-1003915149928"}
    for p_id, channel in prov_map.items():
        sources = SMART_SOURCES[p_id] + [m[0] for m in manuals if m[1] == p_id]
        for src in set(sources):
            posts = scrape_tg(src)
            for p in posts:
                h = hashlib.md5(str(p['id']).encode()).hexdigest()
                cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
                if not cur.fetchone():
                    cat = ai_classify(p['text'])
                    caption = f"📌 <b>{cat}</b>\n📍 استان {p_id}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع اصلی</a>"
                    kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                    
                    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    res = None
                    try:
                        if p['type'] == "video" and p['media']:
                            v_data = requests.get(p['media']).content
                            res = requests.post(tg_url+"sendVideo", data={"chat_id":channel, "caption":caption, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", v_data)})
                        elif p['type'] == "photo" and p['media']:
                            res = requests.post(tg_url+"sendPhoto", json={"chat_id":channel, "photo":p['media'], "caption":caption, "parse_mode":"HTML", "reply_markup":kb})
                        else:
                            res = requests.post(tg_url+"sendMessage", json={"chat_id":channel, "text":caption, "parse_mode":"HTML", "reply_markup":kb})

                        if res and res.status_code == 200:
                            m_id = res.json()['result']['message_id']
                            cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                            cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s,%s)", (h, channel, str(m_id), p['text'][:400], p_id, p['type']))
                            conn.commit()
                    except: continue
    cur.close(); conn.close()

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
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"], "text": "⏳ در حال بازنویسی..."})
            prompt = f"این خبر را با واژگان انقلابی (رژیم، قیام، کانون‌های شورشی) بازنویسی کن. فقط متن نهایی را بده:\n{title}"
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", json={"contents": [{"parts": [{"text": prompt}]}]})
            new_txt = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            
            method = "editMessageCaption" if m_type != "text" else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", 
                         json={"chat_id": c_id, "message_id": int(m_id), "caption" if m_type != "text" else "text": f"✊ <b>نسخه مقاومت</b>\n\n{new_txt}", "parse_mode": "HTML"})
        cur.close(); conn.close()
    return "OK"

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check started."

@app.route('/')
def home(): return "Bot Online v17"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
