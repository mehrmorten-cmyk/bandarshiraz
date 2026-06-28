import os, json, time, psycopg2, hashlib, threading, requests, re, io
from bs4 import BeautifulSoup
from flask import Flask, request

# تنظیمات اصلی
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ۱۱ موضوع استراتژیک شما
CATEGORIES = [
    "۱. اعتراضات و تجمع‌های مردمی", "۲. رویدادهای امنیتی", "۳. بازداشت‌ها و پرونده‌های حقوق بشری",
    "۴. نان، آرد و کالاهای اساسی", "۵. سوخت (بنزین و گازوئیل)", "۶. دارو و خدمات درمانی",
    "۷. آب، برق و گاز", "۸. اقتصاد، بازار و معیشت", "۹. اخبار شهرستان‌ها", "۱۰. جمع‌بندی", "۱۱. منابع"
]

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "tg_sources": ["shiraz_online", "akhbarshiraz", "asrshiraz", "shiraz_ma"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "tg_sources": ["hormozgan_online", "bndonline", "akhbar_hormozgan", "hmd_news"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def ai_classify(text):
    """هوش مصنوعی خبر را در یکی از ۱۱ دسته قرار می‌دهد"""
    if not GEMINI_API_KEY: return "سایر اخبار"
    prompt = f"این متن را فقط در یکی از این ۱۱ دسته قرار بده و فقط نام دسته را بنویس:\n{', '.join(CATEGORIES)}\n\nمتن خبر:\n{text[:500]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۹. اخبار شهرستان‌ها"

def download_file(url):
    """دانلود فایل برای آپلود مجدد در تلگرام"""
    try:
        r = requests.get(url, stream=True, timeout=30)
        if r.status_code == 200: return r.content
    except: return None
    return None

def scrape_tg_full(tg_user):
    """رصد کامل محتوا (متن، عکس، ویدیو) از وب تلگرام"""
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        soup = BeautifulSoup(requests.get(url, timeout=15).text, 'html.parser')
        msgs = soup.find_all('div', class_='tgme_widget_message_wrap')
        for m in msgs[-5:]:
            post = {"text": "", "media_url": None, "type": "text", "link": ""}
            
            # استخراج متن
            txt_div = m.find('div', class_='tgme_widget_message_text')
            if txt_div: post["text"] = txt_div.get_text(separator="\n").strip()
            if not post["text"]: continue

            # استخراج ویدیو
            video_tag = m.find('video')
            if video_tag:
                post["media_url"] = video_tag.get('src')
                post["type"] = "video"
            
            # استخراج عکس (اگر ویدیو نبود)
            if not post["media_url"]:
                photo_a = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo_a:
                    style = photo_a.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", style)
                    if match:
                        post["media_url"] = match.group(1)
                        post["type"] = "photo"

            # لینک پست
            msg_div = m.find('div', class_='tgme_widget_message')
            if msg_div: post["link"] = f"https://t.me/{msg_div.get('data-post')}"
            
            items.append(post)
    except: pass
    return items

def run_check():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        posts = []
        for src in config['tg_sources']: posts.extend(scrape_tg_full(src))
        
        for p in posts:
            h = hashlib.md5(p['text'][:100].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                category = ai_classify(p['text'])
                caption = f"📌 <b>{category}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='{p['link']}'>منبع اصلی</a>"
                kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                
                # ارسال هوشمند (عکس، ویدیو یا متن)
                base_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                success = False
                
                if p['type'] == "video" and p['media_url']:
                    file_data = download_file(p['media_url'])
                    if file_data:
                        r = requests.post(base_url + "sendVideo", data={"chat_id": config['channel'], "caption": caption, "parse_mode": "HTML", "reply_markup": json.dumps(kb)}, files={"video": ("video.mp4", file_data)})
                        success = r.status_code == 200
                
                if not success and p['type'] == "photo" and p['media_url']:
                    r = requests.post(base_url + "sendPhoto", json={"chat_id": config['channel'], "photo": p['media_url'], "caption": caption, "parse_mode": "HTML", "reply_markup": kb})
                    success = r.status_code == 200
                
                if not success:
                    r = requests.post(base_url + "sendMessage", json={"chat_id": config['channel'], "text": caption, "parse_mode": "HTML", "reply_markup": kb})
                    success = r.status_code == 200

                if success:
                    m_id = r.json()['result']['message_id']
                    cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                    cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), p['text'][:200], config['name']))
                    conn.commit()
            cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check cycle started with Media Support."

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "callback_query" in data:
        cb = data["callback_query"]; h = cb["data"][3:]
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT title, channel_id, msg_id, prov FROM msg_logs WHERE hash = %s", (h,))
        row = cur.fetchone()
        if row:
            title, c_id, m_id, prov = row
            prompt = f"این خبر را طبق پروتکل مقاومت بازنویسی کن:\n{title}"
            res = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}", json={"contents": [{"parts": [{"text": prompt}]}]})
            new_txt = res.json()['candidates'][0]['content']['parts'][0]['text']
            
            # تشخیص اینکه پیام کپشن دارد یا فقط متن است
            method = "editMessageCaption" if "rw" in cb["data"] else "editMessageText"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", 
                         json={"chat_id": c_id, "message_id": int(m_id), "caption" if method=="editMessageCaption" else "text": f"✊ <b>نسخه مقاومت ({prov})</b>\n\n{new_txt.strip()}", "parse_mode": "HTML"})
        cur.close(); conn.close()
    return "OK"

@app.route('/')
def home(): return "Bot Media Engine Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
