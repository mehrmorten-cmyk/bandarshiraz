import os, time, sqlite3, hashlib, requests, re, logging, threading
from bs4 import BeautifulSoup
from flask import Flask
from datetime import datetime, timedelta

# تنظیمات به سبک ویکتور
BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
FARS_CHANNEL = "-1004352884396"
BND_CHANNEL = "-1003915149928"
DB_PATH = "bot_data.db"

PROVINCES = {
    "fars": {
        "channel": FARS_CHANNEL,
        "sources": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars", "shiraz_online", "shiraz_ma", "sums1401", "shiraz_news24"]
    },
    "hormozgan": {
        "channel": BND_CHANNEL,
        "sources": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today", "bandar_news"]
    }
}

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    return conn

def download_media(url):
    """متد دانلود مستقیم ویکتور"""
    try:
        r = requests.get(url, stream=True, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200: return r.content
    except: return None
    return None

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        now = datetime.now()
        for w in reversed(msgs[-15:]):
            m = w.find("div", class_="tgme_widget_message")
            t_tag = w.find("time")
            if not m or not t_tag: continue
            
            dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00')).replace(tzinfo=None)
            if now - dt > timedelta(hours=24): continue # فقط ۲۴ ساعت اخیر

            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            
            # استخراج مدیا به سبک ویکتور
            v = m.find('video')
            if v: post["media"] = v.get('src'); post["type"] = "video"
            else:
                ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                if ph:
                    st = ph.get('style', '')
                    match = re.search(r"url\('([^']+)'\)", st)
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            
            if post["text"] or post["media"]: items.append(post)
    except: pass
    return items

def run_sync():
    conn = get_db()
    for p_id, config in PROVINCES.items():
        for src in config['sources']:
            posts = scrape_tg(src)
            for p in posts:
                # هش محتوایی برای جلوگیری از تکرار (بند ۴ توافق)
                content_hash = hashlib.md5(re.sub(r'\s+', '', p['text'][:60]).encode()).hexdigest()
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen WHERE hash = ?", (content_hash,))
                if not cur.fetchone():
                    cap = f"📍 <b>استان {p_id.upper()}</b>\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع خبر</a>"
                    try:
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        res = None
                        if p['media']:
                            m_data = download_media(p['media'])
                            if m_data:
                                method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                                res = requests.post(tg_url+method, data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML"}, files={p['type']: ("file", m_data)})
                        
                        if not res or res.status_code != 200:
                            res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})

                        if res.status_code == 200:
                            cur.execute("INSERT INTO seen (hash) VALUES (?)", (content_hash,))
                            conn.commit()
                    except: pass
                time.sleep(1)
    conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_sync).start()
    return "OK"

@app.route('/')
def home(): return "VIKTOR_STABLE_V1"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
