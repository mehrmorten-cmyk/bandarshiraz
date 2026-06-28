import os, json, time, psycopg2, logging, hashlib, threading, requests, re
from xml.etree import ElementTree
from bs4 import BeautifulSoup
from flask import Flask, request

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

CATEGORIES = [
    "۱. اعتراضات و تجمع‌های مردمی", "۲. رویدادهای امنیتی", "۳. بازداشت‌ها و پرونده‌های حقوق بشری",
    "۴. نان، آرد و کالاهای اساسی", "۵. سوخت (بنزین و گازوئیل)", "۶. دارو و خدمات درمانی",
    "۷. آب، برق و گاز", "۸. اقتصاد، بازار و معیشت", "۹. اخبار شهرستان‌ها", "۱۰. جمع‌بندی", "۱۱. منابع"
]

PROVINCES = {
    "fars": {
        "name": "فارس", "channel": "-1004352884396",
        "tg": ["shiraz_online", "akhbarshiraz", "asrshiraz", "fars_news_fars"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8"]
    },
    "hormozgan": {
        "name": "هرمزگان", "channel": "-1003915149928",
        "tg": ["bndonline", "hormozgan_online", "akhbar_hormozgan"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def classify_news(title):
    """دسته‌بندی لحظه‌ای خبر در یکی از ۱۱ گروه"""
    if not GEMINI_API_KEY: return "سایر اخبار"
    prompt = f"این خبر را فقط در یکی از این دسته‌ها قرار بده: {', '.join(CATEGORIES)}. فقط نام دسته را بنویس:\n{title}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "۹. اخبار شهرستان‌ها"

def scrape_tg_media(tg_user):
    """استخراج متن، عکس و ویدیو از تلگرام"""
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        soup = BeautifulSoup(requests.get(url, timeout=15).text, 'html.parser')
        msgs = soup.find_all('div', class_='tgme_widget_message_wrap')
        for m in msgs[-5:]:
            data = {"title": "", "link": "", "media": None, "type": "text"}
            # استخراج متن
            txt_area = m.find('div', class_='tgme_widget_message_text')
            if not txt_area: continue
            data["title"] = txt_area.get_text(separator=" ").strip()
            
            # استخراج عکس
            photo = m.find('a', class_='tgme_widget_message_photo_wrap')
            if photo:
                style = photo.get('style', '')
                img_url = re.search(r"background-image:url\('(.*)'\)", style)
                if img_url:
                    data["media"] = img_url.group(1)
                    data["type"] = "photo"
            
            # استخراج ویدیو (کاور ویدیو)
            video = m.find('i', class_='tgme_widget_message_video_player')
            if video:
                data["type"] = "video" # تلگرام وب اجازه دانلود مستقیم ویدیو نمی‌دهد، کاور را می‌فرستیم
            
            data["link"] = f"https://t.me/{tg_user}"
            items.append(data)
    except: pass
    return items

def run_check():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT)")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        findings = []
        for tg in config['tg']: findings.extend(scrape_tg_media(tg))
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=10).content)
                for i in root.findall(".//item")[:5]:
                    findings.append({"title": i.findtext("title"), "link": i.findtext("link"), "media": None, "type": "text"})
            except: continue

        for news in findings:
            h = hashlib.md5(news['title'][:100].encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
            if not cur.fetchone():
                category = classify_news(news['title'])
                caption = f"📌 <b>{category}</b>\n📍 استان {config['name']}\n\n🔹 {news['title'][:800]}\n\n🔗 <a href='{news['link']}'>منبع</a>"
                kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                
                # ارسال مدیا یا متن
                tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                payload = {"chat_id": config['channel'], "caption": caption, "parse_mode": "HTML", "reply_markup": kb}
                
                if news['media']:
                    payload["photo"] = news['media']
                    r = requests.post(tg_api + "sendPhoto", json=payload)
                else:
                    payload["text"] = caption
                    del payload["caption"]
                    r = requests.post(tg_api + "sendMessage", json=payload)
                
                if r.status_code == 200:
                    m_id = r.json()['result']['message_id']
                    cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                    cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s)", (h, config['channel'], str(m_id), news['title'], config['name']))
                    conn.commit()
            cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_check).start()
    return "Check Started"

@app.route('/webhook', methods=['POST'])
def webhook():
    # منطق بازنویسی Gemini (مشابه نسخه های قبل)
    data = request.json
    if "callback_query" in data:
        cb = data["callback_query"]; h = cb["data"][3:]
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT title, channel_id, msg_id, prov FROM msg_logs WHERE hash = %s", (h,))
        row = cur.fetchone()
        if row:
            title, c_id, m_id, prov = row
            prompt = f"این خبر را با پروتکل مقاومت بازنویسی کن:\n{title}"
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            resp = requests.post(api_url, json={"contents": [{"parts": [{"text": prompt}]}]})
            new_txt = resp.json()['candidates'][0]['content']['parts'][0]['text']
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageCaption" if "rw" in cb["data"] else f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", 
                         json={"chat_id": c_id, "message_id": int(m_id), "caption" if "photo" in row else "text": f"✊ <b>نسخه مقاومت</b>\n\n{new_txt.strip()}", "parse_mode": "HTML"})
        cur.close(); conn.close()
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
