import os, json, time, psycopg2, hashlib, threading, requests, re, sys, logging, io
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from xml.etree import ElementTree
from datetime import datetime, timedelta

# تنظیمات لاگ حرفه‌ای
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(message)s')
logger = logging.getLogger("MASTER_OSINT_FINAL")

# تنظیمات محیطی
BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

HUB_CATEGORIES = [
    "۱. 🚨 اعتراضات و مطالبات مردمی", "۲. ⚖️ حقوق بشر و امنیتی", "۳. 🚧 خدمات شهری و زیرساخت",
    "۴. 💰 معیشت و کالاهای اساسی", "۵. 🏥 دارو و سلامت", "۶. 🌦 هواشناسی و جاده",
    "۷. 🎓 مدارس و دانشگاه", "۸. 💼 استخدام و اشتغال", "۹. 🗝 نیازمندی‌ها و دیوار",
    "۱۰. 🔍 گم‌شده‌ها و پیداشده‌ها", "۱۱. 🎭 فرهنگی، گردشگری و رسم و رسوم"
]

PROVINCES = {
    "fars": {
        "name": "فارس و شیراز", "channel": "-1004352884396",
        "tg": ["akhbarfars", "shiraz_news", "YeRoozeShiraz", "sums1401", "shiraztopnews", "FouriFars", "FarsFouri", "avaye_shiraz", "shiraz_news24", "shiraz_salam", "Shiraz_Fouri"],
        "search_query": "شیراز OR فارس OR مرودشت OR کازرون OR 'باغ ارم' OR 'تخت جمشید' OR ممسنی"
    },
    "hormozgan": {
        "name": "هرمزگان و بندرعباس", "channel": "-1003915149928",
        "tg": ["hormozgan_online", "bndonline", "bandarabbasnews", "akhbar_hormozgan", "hormozgan_today", "bandar_news", "bnd_wall"],
        "search_query": "بندرعباس OR هرمزگان OR قشم OR کیش OR میناب OR 'بندر لنگه' OR 'مراسم زار'"
    }
}

app = Flask(__name__)

def get_db(): return psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=15)

def init_db():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen_final_v1 (hash TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT NOW())")
        conn.commit(); cur.close(); conn.close()
    except: pass

def ai_curator(text, province):
    """هوش مصنوعی با دانش اطلس جغرافیایی و فرهنگی ایران"""
    if not GEMINI_API_KEY: return HUB_CATEGORIES[10], "گزارش جدید"
    prompt = f"""تو یک متخصص جغرافیا، فرهنگ و اخبار استان {province} هستی.
    متن زیر را تحلیل کن. اگر مربوط به محلات، شهرها، رسم و رسوم یا حوادث این استان نیست، بگو NO.
    اگر هست، از این لیست فقط یک دسته را انتخاب کن: {', '.join(HUB_CATEGORIES)}.
    همچنین یک تیتر ۵ کلمه‌ای جنجالی بساز.
    فرمت پاسخ: CATEGORY | TITLE
    متن: {text[:600]}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12)
        res = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        if "NO" in res.upper(): return None, None
        parts = res.split('|')
        return (parts[0].strip(), parts[1].strip()) if len(parts) > 1 else (HUB_CATEGORIES[10], res)
    except: return HUB_CATEGORIES[10], "گزارش جدید"

def clean_html(text):
    """حذف تگ‌های مخرب برای جلوگیری از خطای تلگرام"""
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

def scrape_tg(user):
    """رصد تلگرام با فیلتر دقیق ۲۴ ساعته و استخراج مدیا"""
    items = []
    try:
        url = f"https://t.me/s/{user}"
        soup = BeautifulSoup(requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"}).text, 'html.parser')
        msgs = soup.find_all("div", class_="tgme_widget_message_wrap")
        now = datetime.now()
        for w in msgs[-15:]:
            m = w.find("div", class_="tgme_widget_message")
            t_tag = w.find("time")
            if not m or not t_tag: continue
            
            # فیلتر ۲۴ ساعت واقعی
            dt = datetime.fromisoformat(t_tag.get("datetime").replace('Z', '+00:00')).replace(tzinfo=None)
            if now - dt > timedelta(hours=24): continue

            post = {"text": "", "media": None, "type": "text", "id": m.get("data-post"), "link": f"https://t.me/{m.get('data-post')}"}
            txt_div = m.find("div", class_="tgme_widget_message_text")
            if txt_div: post["text"] = txt_div.get_text(separator="\n").strip()
            
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

def run_osint_engine():
    init_db()
    for p_id, config in PROVINCES.items():
        logger.info(f"--- 📡 SCANNING {config['name'].upper()} ---")
        pool = []
        # ۱. منابع اختصاصی تلگرام
        for src in config['tg']: pool.extend(scrape_tg(src))
        
        # ۲. جستجوی سراسری (وب و اجتماعی)
        try:
            from urllib.parse import quote
            g_url = f"https://news.google.com/rss/search?q={quote(config['search_query'])}+when:1d&hl=fa&gl=IR&ceid=IR:fa"
            root = ElementTree.fromstring(requests.get(g_url, timeout=15).content)
            for i in root.findall(".//item")[:10]:
                pool.append({"text": i.findtext("title"), "link": i.findtext("link"), "type": "text", "media": None, "id": i.findtext("link")})
        except: pass

        for p in pool:
            # حذف تکراری بر اساس محتوا (بند ۴ توافق)
            content_hash = hashlib.md5(re.sub(r'\s+', '', p['text'][:60]).encode()).hexdigest()
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM seen_final_v1 WHERE hash = %s", (content_hash,))
            if not cur.fetchone():
                cat, title = ai_curator(p['text'], config['name'])
                if cat:
                    cap = f"<b>{clean_html(cat)}</b>\n📌 <b>{clean_html(title)}</b>\n\n{clean_html(p['text'][:850])}\n\n🔗 <a href='{p['link']}'>منبع خبر</a>"
                    try:
                        tg_api = f"https://api.telegram.org/bot{BOT_TOKEN}/"
                        res = None
                        if p['type'] != "text" and p['media']:
                            m_data = requests.get(p['media'], timeout=25).content
                            method = "sendVideo" if p['type'] == "video" else "sendPhoto"
                            res = requests.post(tg_api+method, data={"chat_id":config['channel'], "caption":cap, "parse_mode":"HTML"}, files={p['type']: m_data})
                        
                        if not res or res.status_code != 200:
                            res = requests.post(tg_api+"sendMessage", json={"chat_id":config['channel'], "text":cap, "parse_mode":"HTML"})
                        
                        if res.status_code == 200:
                            cur.execute("INSERT INTO seen_final_v1 (hash) VALUES (%s)", (content_hash,))
                            conn.commit()
                    except: pass
            cur.close(); conn.close()
            time.sleep(2)

@app.route('/check')
def check():
    threading.Thread(target=run_osint_engine).start()
    return "OK"

@app.route('/')
def home():
    return "BOT ACTIVE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
