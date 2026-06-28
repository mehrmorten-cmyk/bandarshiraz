import os, json, time, psycopg2, hashlib, threading, requests, re, io, sys
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree

# تنظیم لاگ برای مشاهده در رندر
logging_format = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format=logging_format)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "tg_sources": ["shiraz_online", "akhbarshiraz", "asrshiraz"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "tg_sources": ["hormozgan_online", "bndonline", "akhbar_hormozgan"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
        cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: logging.error(f"DB Error: {e}")

def ai_classify(text):
    """دسته‌بندی هوشمند در ۱۱ موضوع شما"""
    prompt = f"فقط شماره و نام یکی از این ۱۱ دسته را برای متن زیر انتخاب کن: ۱.اعتراضات، ۲.امنیتی، ۳.حقوق بشری، ۴.نان، ۵.سوخت، ۶.دارو، ۷.آب و برق، ۸.اقتصاد، ۹.شهرستان‌ها، ۱۰.جمع‌بندی، ۱۱.منابع\n\nمتن خبر:\n{text[:300]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۹. اخبار شهرستان‌ها"

def scrape_tg_v2(tg_user):
    """استخراج پیشرفته مدیا مطابق متد ویکتور"""
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        widgets = soup.find_all("div", class_="tgme_widget_message_wrap")
        
        for w in widgets[-5:]:
            msg = w.find("div", class_="tgme_widget_message")
            if not msg: continue
            
            post = {"text": "", "media_url": None, "type": "text", "id": msg.get("data-post")}
            
            # متن
            txt_div = msg.find("div", class_="tgme_widget_message_text")
            if txt_div: post["text"] = txt_div.get_text(separator="\n").strip()
            
            # ویدیو (متد ویکتور)
            video_tag = msg.find('video')
            if video_tag:
                post["media_url"] = video_tag.get('src')
                post["type"] = "video"
            
            # عکس (اگر ویدیو نبود)
            if not post["media_url"]:
                photo_a = msg.find('a', class_='tgme_widget_message_photo_wrap')
                if photo_a:
                    style = photo_a.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", style)
                    if match:
                        post["media_url"] = match.group(1)
                        post["type"] = "photo"
            
            if post["text"] or post["media_url"]:
                items.append(post)
    except Exception as e: logging.error(f"Scrape Error @{tg_user}: {e}")
    return items

def run_check():
    init_db()
    logging.info("🔎 شروع بررسی منابع...")
    for p_id, config in PROVINCES.items():
        all_posts = []
        # چک تلگرام
        for src in config['tg_sources']:
            found = scrape_tg_v2(src)
            logging.info(f"--- کانال @{src}: {len(found)} پست یافت شد.")
            all_posts.extend(found)
        
        # چک RSS
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                for i in root.findall(".//item")[:5]:
                    all_posts.append({"text": i.findtext("title"), "id": i.findtext("link"), "type": "text", "media_url": None})
            except: continue

        for p in all_posts:
            h = hashlib.md5(str(p['id']).encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                category = ai_classify(p['text'])
                caption = f"📌 <b>{category}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id'] if p['id'] else ''}'>منبع</a>"
                kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                
                # ارسال
                tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                res = None
                if p['type'] == "video" and p['media_url']:
                    video_data = requests.get(p['media_url']).content
                    res = requests.post(tg_api + "sendVideo", data={"chat_id": config['channel'], "caption": caption, "parse_mode": "HTML", "reply_markup": json.dumps(kb)}, files={"video": ("video.mp4", video_data)})
                elif p['type'] == "photo" and p['media_url']:
                    res = requests.post(tg_api + "sendPhoto", json={"chat_id": config['channel'], "photo": p['media_url'], "caption": caption, "parse_mode": "HTML", "reply_markup": kb})
                else:
                    res = requests.post(tg_api + "sendMessage", json={"chat_id": config['channel'], "text": caption, "parse_mode": "HTML", "reply_markup": kb})

                if res and res.status_code == 200:
                    m_id = res.json()['result']['message_id']
                    cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                    cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:200], config['name']))
                    conn.commit()
            cur.close(); conn.close()
    logging.info("✅ بررسی تمام شد.")

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check started. Watch Logs."

@app.route('/webhook', methods=['POST'])
def webhook():
    # بخش بازنویسی (بدون تغییر)
    data = request.json
    if "callback_query" in data:
        cb = data["callback_query"]; h = cb["data"][3:]
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT title, channel_id, msg_id, prov FROM msg_logs WHERE hash = %s", (h,))
        row = cur.fetchone()
        if row:
            title, c_id, m_id, prov = row
            prompt = f"این متن را با پروتکل مقاومت بازنویسی کن:\n{title}"
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", json={"contents": [{"parts": [{"text": prompt}]}]})
            new_txt = r.json()['candidates'][0]['content']['parts'][0]['text']
            method = "editMessageCaption" if "rw" in cb["data"] else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json={"chat_id": c_id, "message_id": int(m_id), "caption" if "Caption" in method else "text": f"✊ <b>نسخه مقاومت</b>\n\n{new_txt.strip()}", "parse_mode": "HTML"})
        cur.close(); conn.close()
    return "OK"

@app.route('/')
def home(): return "Bot Online v15"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
