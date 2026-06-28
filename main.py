import os, json, time, psycopg2, hashlib, threading, requests, re, sys
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree

# تنظیمات لاگ
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

# منابع پیش‌فرض ایجنت (هوشمند)
SMART_SOURCES = {
    "fars": ["shiraz_online", "akhbarshiraz", "asrshiraz", "fars_news_fars"],
    "hormozgan": ["hormozgan_online", "bndonline", "akhbar_hormozgan", "hmd_news"]
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS manual_sources (username TEXT PRIMARY KEY, province TEXT)")
    conn.commit(); cur.close(); conn.close()

def ai_classify(text):
    """دسته‌بندی در ۱۱ موضوع استراتژیک"""
    prompt = f"متن خبر را فقط در یکی از این ۱۱ دسته قرار بده و فقط نام دسته را برگردان:\n{', '.join(CATEGORIES)}\n\nخبر:\n{text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۹. اخبار شهرستان‌ها"

def scrape_tg(tg_user):
    """رصد مدیا و متن مطابق متد ویکتور"""
    items = []
    try:
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
    except: pass
    return items

def run_check():
    init_db()
    conn = get_db(); cur = conn.cursor()
    # خواندن منابع دستی اضافه شده از تلگرام یا Env
    cur.execute("SELECT username, province FROM manual_sources")
    manuals = cur.fetchall()
    
    # ترکیب منابع هوشمند و دستی
    for p_id, config in {"fars": "-1004352884396", "hormozgan": "-1003915149928"}.items():
        sources = SMART_SOURCES[p_id] + [m[0] for m in manuals if m[1] == p_id]
        
        for src in set(sources): # حذف همپوشانی
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
                    if p['type'] == "video" and p['media']:
                        video_data = requests.get(p['media']).content
                        res = requests.post(tg_url+"sendVideo", data={"chat_id":config, "caption":caption, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", video_data)})
                    elif p['type'] == "photo" and p['media']:
                        res = requests.post(tg_url+"sendPhoto", json={"chat_id":config, "photo":p['media'], "caption":caption, "parse_mode":"HTML", "reply_markup":kb})
                    else:
                        res = requests.post(tg_url+"sendMessage", json={"chat_id":config, "text":caption, "parse_mode":"HTML", "reply_markup":kb})

                    if res and res.status_code == 200:
                        m_id = res.json()['result']['message_id']
                        cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                        cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s)", (h, config, str(m_id), p['text'][:200], p_id))
                        conn.commit()
    cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check started."

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        msg = data["message"]; text = msg["text"]
        # دستور اضافه کردن کانال جدید از تلگرام: /add fars channel_id
        if text.startswith("/add"):
            parts = text.split()
            if len(parts) == 3:
                conn = get_db(); cur = conn.cursor()
                cur.execute("INSERT INTO manual_sources VALUES (%s, %s) ON CONFLICT DO NOTHING", (parts[2], parts[1]))
                conn.commit(); cur.close(); conn.close()
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":msg["chat"]["id"], "text":"✅ منبع با موفقیت اضافه شد."})

    if "callback_query" in data:
        # منطق بازنویسی (بدون تغییر)
        pass
    return "OK"

@app.route('/')
def home(): return "Hybrid News Bot Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
