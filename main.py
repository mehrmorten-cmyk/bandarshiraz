import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# لیست جامع ۱۱‌گانه (ادغام اهداف سیاسی، امنیتی و رفاهی)
UNIFIED_CATEGORIES = [
    "۱. 🚨 اعتراضات، تجمعات و مطالبات مردمی",
    "۲. ⚖️ حقوق بشر، بازداشت‌ها و رویدادهای امنیتی",
    "۳. 🚧 خدمات شهری، زیرساخت و قطعی‌ها (آسفالت، آب، برق، سوخت)",
    "۴. 💰 معیشت، بازار و کالاهای اساسی (نان، برنج، قیمت‌ها)",
    "۵. 🏥 دارو، درمان و سلامت جامعه",
    "۶. 🌦 هواشناسی، جاده‌ها و محیط زیست",
    "۷. 🎓 مدارس، دانشگاه‌ها و رویدادهای علمی",
    "۸. 💼 استخدام و فرصت‌های شغلی",
    "۹. 🗝 نیازمندی‌ها، آگهی و دیوار استانی",
    "۱۰. 🔍 گم‌شده‌ها و پیداشده‌ها",
    "۱۱. 🎭 فرهنگی، گردشگری و ورزش"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "sources": ["shiraz_online", "akhbarshiraz", "shiraz_ma", "shiraz_neiaz", "divar_shiraz"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "sources": ["hormozgan_online", "bndonline", "bandar_news", "bnd_wall", "bnd_job"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require')

def ai_smart_editor(text, province):
    """هوش مصنوعی محتوا را بر اساس ۱۱ دسته جدید تحلیل و تیترزنی می‌کند"""
    if not GEMINI_API_KEY: return None
    prompt = f"""تو سردبیر ارشد پلتفرم مرجع استان {province} هستی.
    ۱. اگر محتوا مربوط به استان {province} نیست، بگو NO.
    ۲. اگر هست، یکی از این ۱۱ دسته را انتخاب کن: {', '.join(UNIFIED_CATEGORIES)}.
    ۳. یک تیتر کوتاه و جنجالی (حداکثر ۶ کلمه) بنویس.
    
    فرمت پاسخ:
    CAT: [نام دسته]
    HEAD: [تیتر]
    
    محتوا: {text[:600]}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12)
        res = resp.json()['candidates'][0]['content']['parts'][0]['text']
        if "NO" in res.upper(): return None
        return res
    except: return f"CAT: {UNIFIED_CATEGORIES[0]}\nHEAD: گزارش مردمی"

def scrape_tg(tg_user):
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

def run_pro_cycle():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_news (hash TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS msg_logs (hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, title TEXT, prov TEXT, type TEXT)")
    conn.commit(); cur.close(); conn.close()

    for p_id, config in PROVINCES.items():
        for src in config['sources']:
            posts = scrape_tg(src)
            for p in posts:
                h = hashlib.md5(p['id'].encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_news WHERE hash = %s", (h,))
                if not cur.fetchone():
                    ai_res = ai_smart_editor(p['text'], config['name'])
                    if ai_res:
                        cat = re.search(r"CAT: (.*)", ai_res).group(1).strip()
                        head = re.search(r"HEAD: (.*)", ai_res).group(1).strip()
                        
                        caption = f"<b>{cat}</b>\n📌 <b>{head}</b>\n\n{p['text'][:900]}\n\n🔗 <a href='https://t.me/{p['id']}'>لینک منبع</a>"
                        kb = {"inline_keyboard": [[{"text": "📝 بازنویسی مقاومت", "callback_data": f"rw:{h}"}]]}
                        
                        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        try:
                            res = None
                            if p['type'] == "video" and p['media']:
                                video_data = requests.get(p['media']).content
                                res = requests.post(tg_url+"sendVideo", data={"chat_id":config['channel'], "caption":caption, "parse_mode":"HTML", "reply_markup":json.dumps(kb)}, files={"video":("v.mp4", video_data)})
                            elif p['type'] == "photo" and p['media']:
                                res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":caption, "parse_mode":"HTML", "reply_markup":kb})
                            else:
                                res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":caption, "parse_mode":"HTML", "reply_markup":kb})
                            
                            if res and res.status_code == 200:
                                cur.execute("INSERT INTO seen_news VALUES (%s)", (h,))
                                cur.execute("INSERT INTO msg_logs VALUES (%s,%s,%s,%s,%s,%s)", (h, config['channel'], str(res.json()['result']['message_id']), head, p_id, p['type']))
                                conn.commit()
                        except: continue
                cur.close(); conn.close()

@app.route('/check')
def check():
    threading.Thread(target=run_pro_cycle).start()
    return "The Ultimate Province Hub is Syncing..."

@app.route('/webhook', methods=['POST'])
def webhook():
    # منطق بازنویسی (بدون تغییر)
    pass

@app.route('/')
def home(): return "Province Hub v22 Online"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
