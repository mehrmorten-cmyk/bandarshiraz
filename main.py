import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask, request
from xml.etree import ElementTree
from datetime import datetime, timedelta

# پیکربندی حرفه‌ای لاگ
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("PROVINCE_OSINT_FINAL")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# لیست ۱۱‌گانه دقیق و مورد تایید شما
HUB_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات مردمی", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و قطعی‌ها",
    "۴. 💰 معیشت و کالاهای اساسی", "۵. 🏥 دارو، درمان و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام و اشتغال", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها و پیداشده‌ها", "۱۱. 🎭 فرهنگی، گردشگری و ورزش"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "sums1401", "shiraztopnews", "FouriFars", "avaye_shiraz", "shiraz_news24", "shiraz_salam", "dorhamishiraziha", "Shiraz_Fouri"],
        "rss": ["https://www.irna.ir/rss/service/131", "https://www.tasnimnews.com/fa/rss/service/0/8", "https://www.mehrnews.com/rss/service/74"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall"],
        "rss": ["https://www.irna.ir/rss/service/151", "https://www.tasnimnews.com/fa/rss/service/0/13", "https://www.isna.ir/rss/service/77"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def init_db():
    """ایجاد ساختار نهایی و بدون خطا"""
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_final (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        cur.execute("""CREATE TABLE IF NOT EXISTS logs_final (
            hash TEXT PRIMARY KEY, channel_id TEXT, msg_id TEXT, 
            title TEXT, prov TEXT, m_type TEXT, ts TIMESTAMP DEFAULT NOW())""")
        conn.commit(); cur.close(); conn.close()
    except: pass

def extract_image(url):
    """استخراج عکس اصلی خبرگزاری ها (تضمین نمایش عکس برای بندرعباس)"""
    try:
        res = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, 'html.parser')
        og_img = soup.find("meta", property="og:image")
        if og_img: return og_img.get("content")
    except: return None
    return None

def ai_curator(text, province):
    """سردبیر هوشمند: انتخاب دسته و تولید تیتر"""
    if not GEMINI_API_KEY: return HUB_CATEGORIES[0], "گزارش محلی"
    prompt = f"به عنوان سردبیر استان {province}، برای این متن فقط یکی از این دسته‌ها را انتخاب کن: {', '.join(HUB_CATEGORIES)}. همچنین یک تیتر ۵ کلمه‌ای جذاب بساز. فرمت پاسخ: CAT | TITLE. متن: {text[:500]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12)
        res = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        parts = res.split('|')
        return (parts[0].strip(), parts[1].strip()) if len(parts) > 1 else (HUB_CATEGORIES[0], res)
    except: return HUB_CATEGORIES[0], "گزارش جدید"

def scrape_telegram(user):
    """رصد دقیق تلگرام با فیلتر زمانی ۲۴ ساعته واقعی"""
    results = []
    try:
        url = f"https://t.me/s/{user}"
        soup = BeautifulSoup(requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"}).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        now = datetime.now()
        for w in msgs[-15:]:
            m = w.find("div", class_="tgme_widget_message")
            t_tag = w.find("time")
            if not m or not t_tag: continue
            # فیلتر تاریخ سخت‌گیرانه
            dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00')).replace(tzinfo=None)
            if now - dt > timedelta(hours=24): continue
            
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            v = m.find('video')
            if v: post["media"] = v.get('src'); post["type"] = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    match = re.search(r"url\('([^']+)'\)", photo.get('style', ''))
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: results.append(post)
    except: pass
    return results

def run_osint_system():
    init_db()
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {config['name']} ---")
        pool = []
        # ۱. رصد تلگرام اختصاصی
        for src in config['tg']: pool.extend(scrape_telegram(src))
        # ۲. رصد وب (با استخراج عکس)
        for url in config['rss']:
            try:
                root = ElementTree.fromstring(requests.get(url, timeout=12).content)
                for i in root.findall(".//item")[:10]:
                    link = i.findtext("link")
                    img = extract_image(link)
                    pool.append({"text": i.findtext("title"), "id": link, "type": "photo" if img else "text", "media": img, "link": link})
            except: continue

        for p in pool:
            h = hashlib.md5(str(p['id']).encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_final WHERE hash = %s", (h,))
            if not cur.fetchone():
                cat, title = ai_curator(p['text'], config['name'])
                cap = f"<b>{cat}</b>\n📌 <b>{title}</b>\n\n{p['text'][:900]}\n\n🔗 <a href='{p.get('link', 'https://t.me/'+str(p['id']))}'>منبع خبر</a>"
                
                try:
                    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    res = None
                    if p['type'] == "video" and p['media']:
                        res = requests.post(tg_url+"sendVideo", json={"chat_id":config['channel'], "video":p['media'], "caption":cap, "parse_mode":"HTML"})
                    elif p.get('media'):
                        res = requests.post(tg_url+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                    else:
                        res = requests.post(tg_url+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                    
                    if res and res.status_code == 200:
                        cur.execute("INSERT INTO seen_final (hash) VALUES (%s)", (h,))
                        conn.commit()
                        logger.info(f"✅ DISPATCHED: {p['id']}")
                except: pass
            cur.close(); conn.close()
            time.sleep(2)

@app.route('/check')
def check():
    threading.Thread(target=run_osint_system).start()
    return "OSINT Reference Engine v38 is running."

@app.route('/')
def home(): return "V38 ONLINE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
