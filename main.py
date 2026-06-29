import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("FINAL_STABLE")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_TOPICS = ["اعتراضات", "امنیت", "خدمات شهری", "معیشت", "سلامت", "هواشناسی", "مدارس", "استخدام", "نیازمندی", "گمشده", "فرهنگی"]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars", "shiraz_online", "shiraz_ma"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def init_db():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_v42 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit(); cur.close(); conn.close()
    except: pass

def ai_tag(text, province):
    if not GEMINI_API_KEY: return "گزارش"
    prompt = f"سردبیر {province} باش. از این لیست یک دسته انتخاب کن و یک تیتر ۵ کلمه ای بساز. CAT | TITLE. لیست: {','.join(HUB_TOPICS)}. متن: {text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except: return "گزارش جدید"

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        soup = BeautifulSoup(requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"}).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        for w in msgs[-15:]:
            m = w.find("div", class_="tgme_widget_message")
            t_tag = w.find("time")
            if not m or not t_tag: continue
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            
            v = m.find('video')
            if v: post["media"] = v.get('src'); post["type"] = "video"
            else:
                ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                if ph:
                    match = re.search(r"url\('([^']+)'\)", ph.get('style', ''))
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: items.append(post)
    except: pass
    return items

def run_osint():
    init_db()
    for p_id, config in PROVINCES.items():
        for src in config['tg']:
            posts = scrape_tg(src)
            for p in posts:
                # حذف تکراری بر اساس محتوا (بند ۴ توافق)
                content_hash = hashlib.md5(re.sub(r'\s+', '', p['text'][:60]).encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_v42 WHERE hash = %s", (content_hash,))
                if not cur.fetchone():
                    res = ai_tag(p['text'], config['name'])
                    cap = f"<b>{res}</b>\n📍 استان {config['name']}\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع</a>"
                    try:
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        if p['type'] == "video" and p['media']:
                            requests.post(tg_url+"sendVideo", json={"chat_id":config['channel'], "video":p['media'], "caption":cap, "parse_mode":"HTML"})
                        elif p.get('media'):
                            requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                        else:
                            requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                        cur.execute("INSERT INTO seen_v42 (hash) VALUES (%s)", (content_hash,))
                        conn.commit()
                    except: pass
                cur.close(); conn.close()
                time.sleep(1)

@app.route('/check')
def check():
    threading.Thread(target=run_osint).start()
    return "OK" # پاسخ کوتاه برای جلوگیری از خطای کرون‌جاب

@app.route('/')
def home(): return "ACTIVE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
