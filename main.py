import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from bs4 import BeautifulSoup
from flask import Flask
from xml.etree import ElementTree
from datetime import datetime, timedelta

# پیکربندی حرفه‌ای لاگ
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OSINT_ENGINE_V46")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و زیرساخت",
    "۴. 💰 معیشت و بازار", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها", "۱۱. 🎭 فرهنگی و ورزش"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars", "shiraz_online", "shiraz_ma", "sums1401", "shiraz_news24", "Shiraz_Fouri"]
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall"]
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=15)

def clean_html_safe(text):
    """پاکسازی ایمن متن برای جلوگیری از خطاهای پارس تلگرام"""
    if not text: return ""
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

def ai_curator(text, province):
    """هوش مصنوعی با متد تدافعی برای دسته بندی و تیترزنی"""
    if not GEMINI_API_KEY: return "۱۱. عمومی", "گزارش جدید"
    prompt = f"سردبیر استان {province} باش. از لیست زیر یک دسته انتخاب کن و یک تیتر ۵ کلمه‌ای بساز. فقط بنویس: CAT | TITLE\nلیست: {', '.join(HUB_CATEGORIES)}\n\nمتن: {text[:400]}"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        res = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        parts = res.split('|')
        return (parts[0].strip(), parts[1].strip()) if len(parts) > 1 else (HUB_CATEGORIES[0], res)
    except: return "۱۱. عمومی", "گزارش محلی"

def scrape_tg_resilient(user):
    """متد پیمایش معکوس (Bottom-up) با ایمنی ویژگی‌ها"""
    items = []
    try:
        url = f"https://t.me/s/{user}"
        resp = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        
        now = datetime.now()
        # پیمایش از انتهای صفحه به ابتدا (جدیدترین به قدیمی‌ترین)
        for w in reversed(msgs):
            try:
                m = w.find("div", class_="tgme_widget_message")
                t_tag = w.find("time")
                if not m or not t_tag: continue
                
                # فیلتر زمان ۲۴ ساعته واقعی (مانع ورود اخبار ژانویه)
                dt_str = t_tag.get("datetime")
                if not dt_str: continue
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00')).replace(tzinfo=None)
                if now - dt > timedelta(hours=24): break # توقف پیمایش در اولین خبر قدیمی
                
                post_id = m.get("data-post")
                txt_div = m.find("div", class_="tgme_widget_message_text")
                body = txt_div.get_text(separator="\n").strip() if txt_div else ""
                
                media, m_type = None, "text"
                v = m.find('video')
                if v: media = v.get('src'); m_type = "video"
                else:
                    ph = m.find('a', class_='tgme_widget_message_photo_wrap')
                    if ph:
                        match = re.search(r"url\('([^']+)'\)", ph.get('style', ''))
                        if match: media = match.group(1); m_type = "photo"
                
                if body: items.append({"text": body, "media": media, "type": m_type, "id": post_id})
            except: continue # نادیده گرفتن خطای یک پیام و ادامه دادن
    except Exception as e: logger.error(f"Scrape Failed for @{user}: {e}")
    return items

def run_sync_v46():
    # تضمین دیتابیس
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_v46 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit(); cur.close(); conn.close()
    except: pass

    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 OSINT START: {config['name']} ---")
        for src in config['tg']:
            posts = scrape_tg_resilient(src)
            for p in posts:
                h = hashlib.md5(str(p['id']).encode()).hexdigest()
                conn = get_db(); cur = conn.cursor()
                cur.execute("SELECT 1 FROM seen_v46 WHERE hash = %s", (h,))
                if not cur.fetchone():
                    cat, title = ai_curator(p['text'], config['name'])
                    safe_cap = f"<b>{clean_html_safe(cat)}</b>\n📌 <b>{clean_html_safe(title)}</b>\n\n{clean_html_safe(p['text'][:850])}\n\n🔗 <a href='https://t.me/{p['id']}'>منبع</a>"
                    
                    try:
                        tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        sent = False
                        if p['media']:
                            # دانلود با وقفه و ارسال (متد پایدار)
                            m_data = requests.get(p['media'], timeout=25).content
                            method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                            r = requests.post(tg_api + method, data={"chat_id": config['channel'], "caption": safe_cap, "parse_mode": "HTML"}, files={p['type']: m_data})
                            sent = r.status_code == 200
                        
                        if not sent:
                            r = requests.post(tg_api+"sendMessage", json={"chat_id":config['channel'], "text":safe_cap, "parse_mode":"HTML"})
                            sent = r.status_code == 200
                        
                        if sent:
                            cur.execute("INSERT INTO seen_v46 (hash) VALUES (%s)", (h,))
                            conn.commit()
                            logger.info(f"✅ SUCCESS: {p['id']}")
                    except: pass
                cur.close(); conn.close()
                time.sleep(2)

@app.route('/check')
def check():
    threading.Thread(target=run_sync_v46).start()
    return "OSINT ENGINE V46 ACTIVE"

@app.route('/')
def home(): return "STABLE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
