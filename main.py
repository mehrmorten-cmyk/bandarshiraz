import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from urllib.parse import quote

# تنظیمات لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("PROVINCE_HUB")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# تعریف حوزه‌های فعالیت پلتفرم مرجع
HUB_CATEGORIES = [
    "🚨 حوادث و فوریت‌ها", "🚧 خدمات شهری و زیرساخت (آسفالت، برق و...)", 
    "💰 معیشت و بازار", "🎓 مدارس و دانشگاه‌ها", "📢 مطالبات مردمی", 
    "💼 استخدام و فرصت شغلی", "🗝 نیازمندی‌ها و آگهی (دیوار استانی)", 
    "🔍 گم‌شده‌ها و پیداشده‌ها", "🌦 هواشناسی و جاده‌ها", "🎭 فرهنگی و گردشگری"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "sources": [
            "shiraz_online", "akhbarshiraz", "shiraz_it", "shirazi_ha", 
            "shiraz_ma", "shiraz_neiaz", "divar_shiraz", "shiraz_vaghaye"
        ]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "sources": [
            "hormozgan_online", "bndonline", "akhbar_hormozgan", 
            "hormozgan_today", "bandar_news", "bnd_wall", "bnd_job"
        ]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT, type TEXT)")
    conn.commit(); cur.close(); conn.close()

def ai_smart_tagger(text, province):
    """هوش مصنوعی محتوا را تحلیل کرده و برچسب‌گذاری می‌کند"""
    if not GEMINI_API_KEY: return HUB_CATEGORIES[0]
    prompt = f"""تو مدیر پلتفرم مرجع استان {province} هستی. محتوای زیر را تحلیل کن:
    ۱. اگر مربوط به استان {province} نیست، بگو NO.
    ۲. اگر هست، مناسب‌ترین دسته را از این لیست انتخاب کن: {', '.join(HUB_CATEGORIES)}.
    ۳. یک تیتر کوتاه و جذاب (حداکثر ۷ کلمه) برایش بنویس.
    
    پاسخ را دقیقاً با این فرمت بده:
    CATEGORY: [نام دسته]
    TITLE: [تیتر]
    
    محتوا: {text[:600]}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12)
        res = resp.json()['candidates'][0]['content']['parts'][0]['text']
        if "NO" in res.upper(): return None
        return res
    except: return f"CATEGORY: {HUB_CATEGORIES[0]}\nTITLE: گزارش مردمی"

def scrape_tg_hub(tg_user):
    """رصد دقیق پیام‌ها و مدیاها"""
    items = []
    try:
        url = f"https://t.me/s/{tg_user}"
        soup = BeautifulSoup(requests.get(url, timeout=15).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap", limit=15)
        for w in reversed(msgs):
            m = w.find("div", class_="tgme_widget_message")
            if not m: continue
            p_id = m.get("data-post")
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
            if post["text"]: items.append(post)
    except: pass
    return items

def run_hub_cycle():
    init_db()
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 🌐 UPDATING HUB: {config['name']} ---")
        for src in config['sources']:
            posts = scrape_tg_hub(src)
            for p in posts:
                h = hashlib.md5(p['id'].encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
                if not cur.fetchone():
                    # پردازش هوشمند محتوا
                    ai_res = ai_smart_tagger(p['text'], config['name'])
                    if ai_res:
                        cat = re.search(r"CATEGORY: (.*)", ai_res).group(1).strip()
                        title = re.search(r"TITLE: (.*)", ai_res).group(1).strip()
                        
                        caption = f"{cat}\n📍 <b>{title}</b>\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>لینک مستقیم</a>"
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
                                cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                                cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(res.json()['result']['message_id']), title, p_id, p['type']))
                                conn.commit()
                        except: continue
                cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_hub_cycle).start()
    return "Province Reference Hub updated."

@app.route('/webhook', methods=['POST'])
def webhook():
    # منطق بازنویسی (بدون تغییر باقی می‌ماند)
    pass

@app.route('/')
def home(): return "Province Reference Engine v21 Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
