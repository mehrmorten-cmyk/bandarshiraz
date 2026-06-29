import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree
from urllib.parse import quote
from datetime import datetime, timedelta

# تنظیمات لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("STRATEGIC_OSINT_V40")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ۱۱ موضوع مرجع استانی
HUB_TOPICS = [
    "۱. 🚨 اعتراضات و مطالبات", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و آسفالت",
    "۴. 💰 معیشت و نانوایی", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

# کلمات کلیدی تمام شهرستان‌ها برای جستجوی عمیق (بند ۱ و ۲)
FARS_CITIES = "شیراز,مرودشت,کازرون,جهرم,فسا,داراب,لار,فیروزآباد,ممسنی,نی‌ریز,آباده,اقلید,سپیدان,استهبان,کوار,زرین‌دشت,قیروکازرزین,خرم‌بید,بوانات,خرامه,پاسارگاد".split(",")
HORMOZGAN_CITIES = "بندرعباس,میناب,قشم,کیش,بندرلنگه,حاجی‌آباد,رودان,بستک,بندرخمیر,پارسیان,جاسک,سیریک,بشاگرد".split(",")

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "cities": FARS_CITIES,
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars", "FarsFouri", "shiraz_online", "SaberinFars", "shiraz_salam", "Shiraz_Fouri"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "cities": HORMOZGAN_CITIES,
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen_v40 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
    conn.commit(); cur.close(); conn.close()

def ai_tagger(text, province):
    if not GEMINI_API_KEY: return HUB_TOPICS[0], "گزارش جدید"
    prompt = f"به عنوان سردبیر استان {province}، متن زیر را در یکی از این دسته‌ها قرار بده و یک تیتر ۶ کلمه‌ای بساز. فقط بنویس: CAT | TITLE\nدسته‌ها: {', '.join(HUB_TOPICS)}\nمتن: {text[:500]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        res = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        parts = res.split('|')
        return (parts[0].strip(), parts[1].strip()) if len(parts) > 1 else (HUB_TOPICS[0], res)
    except: return HUB_TOPICS[0], "گزارش محلی"

def scrape_tg(user):
    items = []
    try:
        url = f"https://t.me/s/{user}"
        soup = BeautifulSoup(requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"}).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        now = datetime.now()
        for w in msgs[-15:]:
            m = w.find("div", class_="tgme_widget_message")
            t_tag = w.find("time")
            if not m or not t_tag: continue
            dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00')).replace(tzinfo=None)
            if now - dt > timedelta(hours=24): continue # بند ۳ (فقط ۲۴ ساعت اخیر)
            
            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post")}
            txt = m.find("div", class_="tgme_widget_message_text")
            if txt: post["text"] = txt.get_text(separator="\n").strip()
            
            video = m.find('video')
            if video: post["media"] = video.get('src'); post["type"] = "video"
            else:
                photo = m.find('a', class_='tgme_widget_message_photo_wrap')
                if photo:
                    match = re.search(r"url\('([^']+)'\)", photo.get('style', ''))
                    if match: post["media"] = match.group(1); post["type"] = "photo"
            if post["text"]: items.append(post)
    except: pass
    return items

def universal_search(city):
    """جستجوی سراسری اینترنت، اینستا، فیسبوک برای یک شهر (بند ۱ و ۲)"""
    items = []
    try:
        query = f'"{city}" (اعتراض OR گرانی OR حادثه OR قطعی OR دیوار) when:1d'
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=fa&gl=IR&ceid=IR:fa"
        root = ElementTree.fromstring(requests.get(url, timeout=12).content)
        for i in root.findall(".//item")[:5]:
            items.append({"text": i.findtext("title"), "id": i.findtext("link"), "type": "text", "media": None, "link": i.findtext("link")})
    except: pass
    return items

def run_v40_engine():
    init_db()
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {config['name']} ---")
        pool = []
        for tg in config['tg']: pool.extend(scrape_tg(tg))
        for city in config['cities'][:10]: pool.extend(universal_search(city)) # پایش شهرها

        for p in pool:
            # بند ۴ (حذف تکراری بر اساس محتوا)
            content_sample = re.sub(r'\s+', '', p['text'][:50])
            h = hashlib.md5(content_sample.encode()).hexdigest()
            
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_v40 WHERE hash = %s", (h,))
            if not cur.fetchone():
                cat, title = ai_tagger(p['text'], config['name'])
                cap = f"<b>{cat}</b>\n📌 <b>{title}</b>\n\n{p['text'][:900]}\n\n🔗 <a href='{p.get('link', 'https://t.me/'+str(p['id']))}'>منبع اصلی</a>"
                
                try:
                    tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                    res = None
                    if p['type'] == "video" and p['media']:
                        res = requests.post(tg_api+"sendVideo", json={"chat_id":config['channel'], "video":p['media'], "caption":cap, "parse_mode":"HTML"})
                    elif p.get('media'):
                        res = requests.post(tg_api+"sendPhoto", json={"chat_id":config['channel'], "photo":p['media'], "caption":cap, "parse_mode":"HTML"})
                    else:
                        res = requests.post(tg_api+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                    
                    if res and res.status_code == 200:
                        cur.execute("INSERT INTO seen_v40 (hash) VALUES (%s)", (h,))
                        conn.commit()
                except: pass
            cur.close(); conn.close()
            time.sleep(1)

@app.route('/check')
def check():
    threading.Thread(target=run_v40_engine).start()
    return "Master OSINT v40 Started."

@app.route('/')
def home(): return "Reference Hub v40 Active"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
