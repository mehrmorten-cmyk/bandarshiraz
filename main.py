"""
NABZ — نبض خبری
================
بات تلگرام برای جمع‌آوری اخبار از کانال‌های تلگرام و سایت‌های خبری
و ارسال به کانال‌های استانی (فارس + هرمزگان).

ترکیب بهترین امکانات هر دو پروژه:
- روتینگ دو کانالی (استانی)
- دستورات بات (/add, /remove, /list, ...)
- پشتیبانی RSS وب
- آلبوم و مدیای پیشرفته
- SQLite برای ذخیره‌سازی
- فیلتر ۲۴ ساعته
- Flask keep-alive

Environment Variables (Replit Secrets):
- TELEGRAM_BOT_TOKEN: توکن بات از @BotFather
- FARS_CHANNEL_ID: آیدی کانال فارس (مثلاً -1004352884396)
- BND_CHANNEL_ID: آیدی کانال بندرعباس (مثلاً -1003915149928)
- ADMIN_USER_ID: آیدی تلگرام ادمین
"""

import os
import re
import json
import time
import sqlite3
import hashlib
import tempfile
import threading
import traceback
from datetime import datetime, timedelta

import requests
import feedparser
from bs4 import BeautifulSoup
from flask import Flask

# ============================================================
# Configuration — همه از Environment Variables
# ============================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
FARS_CHANNEL = os.environ.get("FARS_CHANNEL_ID", "")
BND_CHANNEL = os.environ.get("BND_CHANNEL_ID", "")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "")

CHECK_INTERVAL_DEFAULT = 1800  # ثانیه (۳۰ دقیقه)
WEB_CHECK_INTERVAL = 1800     # ثانیه (۳۰ دقیقه)
DB_PATH = "nabz_data.db"
MAX_TEXT_LENGTH = 4000
MAX_CAPTION_LENGTH = 1024
MAX_POST_AGE_HOURS = 24  # فقط پست‌های ۲۴ ساعت اخیر

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# منابع پیش‌فرض استانی (مستقیم فوروارد)
DEFAULT_PROVINCES = {
    "fars": {
        "channel": FARS_CHANNEL,
        "label": "فارس",
        "sources": [
            # --- اخبار محلی فارس/شیراز ---
            "akhbarfars", "shiraz_news", "YeRoozeShiraz", "FouriFars",
            "FarsFouri", "avaye_shiraz", "shiraz_online", "shiraz_ma",
            "sums1401", "shiraz_news24", "shiraztopnews", "shirazu_twitter",
            "shirazu1", "SaberinFars", "SUTimes", "LineFars",
            "sSADP", "shorasenfi_shirazunii", "Azad_shiraz", "Shiraz_us",
            "Fars_today", "eghtesadefars", "fars_iau", "News_Neyriz",
            "Shiraz_Fouri", "farsna", "ostan", "javanmardi77", "manmanoo",
        ],
        "web_sources": []
    },
    "hormozgan": {
        "channel": BND_CHANNEL,
        "label": "هرمزگان",
        "sources": [
            "hormozgan_online", "bndonline", "bandarabbasnews",
            "akhbar_hormozgan", "hormozgan_today", "bandar_news",
        ],
        "web_sources": []
    }
}

# ============================================================
# منابع ملی — فیلتر کلمه‌کلیدی (اتوماتیک جداسازی فارس/هرمزگان)
# ============================================================

NATIONAL_SOURCES = [
    "Tasnimnews", "mehrnews", "isna94", "yjcnewschannel",
    "EtemadOnline", "entekhab_ir", "khabaronline_ir",
    "hamshahrinews", "tabnak", "Asriran_press",
    "qudsonline", "BourseNews", "snntv",
    "ir_Protests", "farsivoa",
]

NATIONAL_WEB_SOURCES = [
    {"feed_url": "https://mojahedin.org/rss/", "name": "مجاهدین", "url": "https://mojahedin.org"},
    {"feed_url": "https://www.iranntv.com/rss/", "name": "ایران‌ان‌تی‌وی", "url": "https://www.iranntv.com"},
    {"feed_url": "https://www.hra-news.org/feed/", "name": "فعالان حقوق بشر", "url": "https://www.hra-news.org"},
]

# کلمات کلیدی برای تشخیص استان — هر خبر ملی که شامل این کلمات باشد فوروارد می‌شود
# ⚠️ «فارس» به تنهایی نباشد — چون در «خلیج فارس»، «خبرگزاری فارس»، «فارسی» هم هست
PROVINCE_KEYWORDS = {
    "fars": [
        "شیراز", "استان فارس", "مرودشت", "کازرون", "فسا", "جهرم",
        "نی‌ریز", "نیریز", "داراب", "آباده", "اقلید", "سپیدان",
        "خرامه", "زرقان", "فیروزآباد", "لامرد", "استهبان", "نورآباد",
        "ممسنی", "قیروکارزین", "ارسنجان", "بوانات", "خنج", "گراش",
        "اوز", "سروستان", "کوار", "پاسارگاد", "لارستان",
        "شورای شهر شیراز", "دانشگاه شیراز", "فرمانداری شیراز",
        "فرمانداری لار", "شهرداری شیراز", "تختی شیراز",
    ],
    "hormozgan": [
        "بندرعباس", "بندر عباس", "هرمزگان", "استان هرمزگان", "قشم", "کیش",
        "میناب", "بندر لنگه", "بندرلنگه",
        "حاجی‌آباد", "حاجی آباد", "رودان", "بشاگرد", "جاسک", "سیریک",
        "ابوموسی", "پارسیان", "بستک", "خمیر",
        "تنگه هرمز", "جزیره قشم", "جزیره کیش",
        "فرمانداری بندرعباس", "فرمانداری بندر عباس",
        "شهرداری بندرعباس", "شهرداری بندر عباس",
        "دانشگاه هرمزگان",
    ]
}

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

app = Flask(__name__)
_web_lock = threading.Lock()

# ============================================================
# Database (SQLite)
# ============================================================

_db_lock = threading.Lock()


def get_db():
    """Get a thread-local SQLite connection."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_db()
    cur = conn.cursor()

    # Seen posts (dedup)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seen_posts (
            hash TEXT PRIMARY KEY,
            province TEXT,
            source TEXT,
            post_id TEXT,
            forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Seen web articles (dedup)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seen_web (
            url TEXT PRIMARY KEY,
            province TEXT,
            source_name TEXT,
            forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Province config (dynamic)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS provinces (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            label TEXT NOT NULL
        )
    """)

    # Province sources
    cur.execute("""
        CREATE TABLE IF NOT EXISTS province_sources (
            province_id TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (province_id, source),
            FOREIGN KEY (province_id) REFERENCES provinces(id)
        )
    """)

    # Province web sources
    cur.execute("""
        CREATE TABLE IF NOT EXISTS province_web_sources (
            province_id TEXT NOT NULL,
            feed_url TEXT NOT NULL,
            name TEXT,
            site_url TEXT,
            PRIMARY KEY (province_id, feed_url),
            FOREIGN KEY (province_id) REFERENCES provinces(id)
        )
    """)

    # Bot settings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()

    # Insert default provinces if empty
    cur.execute("SELECT COUNT(*) FROM provinces")
    if cur.fetchone()[0] == 0:
        for pid, pdata in DEFAULT_PROVINCES.items():
            cur.execute(
                "INSERT OR IGNORE INTO provinces (id, channel_id, label) VALUES (?, ?, ?)",
                (pid, pdata["channel"], pdata["label"])
            )
            for src in pdata["sources"]:
                cur.execute(
                    "INSERT OR IGNORE INTO province_sources (province_id, source) VALUES (?, ?)",
                    (pid, src)
                )
            for ws in pdata.get("web_sources", []):
                cur.execute(
                    "INSERT OR IGNORE INTO province_web_sources (province_id, feed_url, name, site_url) VALUES (?, ?, ?, ?)",
                    (pid, ws["feed_url"], ws["name"], ws.get("url", ""))
                )
        conn.commit()

    conn.close()


def get_setting(key, default=""):
    """Read a setting from DB."""
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    """Write a setting to DB."""
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def is_seen(content_hash):
    """Check if a post has been seen."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM seen_posts WHERE hash = ?", (content_hash,)).fetchone()
    conn.close()
    return row is not None


def mark_seen(content_hash, province, source, post_id=""):
    """Mark a post as seen."""
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO seen_posts (hash, province, source, post_id) VALUES (?, ?, ?, ?)",
        (content_hash, province, source, post_id)
    )
    conn.commit()
    conn.close()


def is_web_seen(url):
    """Check if a web article has been seen."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM seen_web WHERE url = ?", (url,)).fetchone()
    conn.close()
    return row is not None


def mark_web_seen(url, province, source_name=""):
    """Mark a web article as seen."""
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO seen_web (url, province, source_name) VALUES (?, ?, ?)",
        (url, province, source_name)
    )
    conn.commit()
    conn.close()


def get_provinces():
    """Get all provinces with their sources."""
    conn = get_db()
    provinces = {}
    for row in conn.execute("SELECT id, channel_id, label FROM provinces").fetchall():
        pid = row["id"]
        provinces[pid] = {
            "channel": row["channel_id"],
            "label": row["label"],
            "sources": [],
            "web_sources": []
        }
        for src in conn.execute("SELECT source FROM province_sources WHERE province_id = ?", (pid,)).fetchall():
            provinces[pid]["sources"].append(src["source"])
        for ws in conn.execute("SELECT feed_url, name, site_url FROM province_web_sources WHERE province_id = ?", (pid,)).fetchall():
            provinces[pid]["web_sources"].append({
                "feed_url": ws["feed_url"],
                "name": ws["name"],
                "url": ws["site_url"]
            })
    conn.close()
    return provinces


def cleanup_old_records(days=7):
    """Remove old seen records to keep DB lean."""
    conn = get_db()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn.execute("DELETE FROM seen_posts WHERE forwarded_at < ?", (cutoff,))
    conn.execute("DELETE FROM seen_web WHERE forwarded_at < ?", (cutoff,))
    conn.commit()
    conn.close()


# ============================================================
# Telegram Bot API Helpers
# ============================================================


def tg_request(method, **params):
    """Make a Telegram Bot API request."""
    url = f"{TELEGRAM_API}/{method}"
    try:
        resp = requests.post(url, json=params, timeout=30)
        result = resp.json()
        if not result.get("ok"):
            print(f"[TG ERROR] {method}: {result.get('description', '?')}")
        return result
    except Exception as e:
        print(f"[TG ERROR] {method}: {e}")
        return {"ok": False, "description": str(e)}


def tg_upload(method, files, data_fields):
    """Telegram Bot API with file upload."""
    url = f"{TELEGRAM_API}/{method}"
    try:
        resp = requests.post(url, files=files, data=data_fields, timeout=60)
        result = resp.json()
        if not result.get("ok"):
            print(f"[TG UPLOAD ERROR] {method}: {result.get('description', '?')}")
        return result
    except Exception as e:
        print(f"[TG UPLOAD ERROR] {method}: {e}")
        return {"ok": False, "description": str(e)}


def send_message(chat_id, text, parse_mode="HTML", disable_preview=True):
    """Send a text message, auto-splitting if too long."""
    if len(text) > MAX_TEXT_LENGTH:
        parts = split_text(text, MAX_TEXT_LENGTH)
        results = []
        for part in parts:
            r = tg_request("sendMessage", chat_id=chat_id, text=part,
                           parse_mode=parse_mode, disable_web_page_preview=disable_preview)
            results.append(r)
            time.sleep(0.5)
        return results[-1] if results else {"ok": False}
    return tg_request("sendMessage", chat_id=chat_id, text=text,
                      parse_mode=parse_mode, disable_web_page_preview=disable_preview)


def download_media(url):
    """Download media from URL, max 20MB."""
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=30, stream=True)
        resp.raise_for_status()
        content = b""
        for chunk in resp.iter_content(chunk_size=8192):
            content += chunk
            if len(content) > 20 * 1024 * 1024:
                print(f"[DOWNLOAD] Too large: {url[:80]}")
                return None, None
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        return content, content_type
    except Exception as e:
        print(f"[DOWNLOAD ERROR] {url[:80]}: {e}")
        return None, None


def send_photo_upload(chat_id, photo_url, caption=""):
    """Download and upload photo to Telegram."""
    photo_data, content_type = download_media(photo_url)
    if not photo_data:
        return {"ok": False, "description": "Download failed"}

    ext = "png" if "png" in (content_type or "") else "jpg"
    if len(caption) > MAX_CAPTION_LENGTH:
        caption = caption[:MAX_CAPTION_LENGTH - 20] + "\n\n..."

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(photo_data)
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            files = {"photo": (f"photo.{ext}", f, content_type or "image/jpeg")}
            return tg_upload("sendPhoto", files=files,
                             data_fields={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def send_video_upload(chat_id, video_url, caption=""):
    """Download and upload video to Telegram (max 50MB)."""
    VIDEO_LIMIT = 50 * 1024 * 1024

    # HEAD check
    try:
        head = requests.head(video_url, headers=SCRAPE_HEADERS, timeout=10, allow_redirects=True)
        cl = int(head.headers.get("Content-Length", 0))
        if cl > VIDEO_LIMIT:
            return {"ok": False, "description": "video_too_large"}
        if head.status_code in (403, 404, 410):
            return {"ok": False, "description": "cdn_blocked"}
    except:
        pass

    tmp_path = None
    content_type = "video/mp4"
    for attempt in range(2):
        if attempt > 0:
            time.sleep(2)
        try:
            resp = requests.get(video_url, headers=SCRAPE_HEADERS, timeout=60, stream=True)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "video/mp4")
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        tmp.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > VIDEO_LIMIT:
                            os.remove(tmp_path)
                            tmp_path = None
                            break
            if tmp_path and downloaded > 0:
                break
            tmp_path = None
        except:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            tmp_path = None

    if not tmp_path:
        return {"ok": False, "description": "Download failed"}

    if len(caption) > MAX_CAPTION_LENGTH:
        caption = caption[:MAX_CAPTION_LENGTH - 20] + "\n\n..."

    try:
        with open(tmp_path, "rb") as f:
            files = {"video": ("video.mp4", f, content_type)}
            return tg_upload("sendVideo", files=files,
                             data_fields={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def send_media_group_upload(chat_id, photo_urls, caption="", video_urls=None):
    """Send multiple photos/videos as album."""
    video_urls = video_urls or []
    tmp_paths = []
    file_handles = []
    try:
        items = ([("photo", u) for u in photo_urls] + [("video", u) for u in video_urls])[:10]

        for idx, (media_type, url) in enumerate(items):
            data, ct = download_media(url)
            if not data:
                continue
            if media_type == "photo":
                ext = "png" if "png" in (ct or "") else "jpg"
                default_ct = "image/jpeg"
            else:
                ext = "mp4"
                default_ct = "video/mp4"
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(data)
                field = f"media{idx}"
                tmp_paths.append((tmp.name, ct or default_ct, media_type, field, ext))

        if not tmp_paths:
            return {"ok": False, "description": "No media downloaded"}

        if len(caption) > MAX_CAPTION_LENGTH:
            caption = caption[:MAX_CAPTION_LENGTH - 20] + "\n\n..."

        media_array = []
        files = {}
        for i, (path, ct, mtype, field, ext) in enumerate(tmp_paths):
            item = {"type": mtype, "media": f"attach://{field}"}
            if i == 0 and caption:
                item["caption"] = caption
                item["parse_mode"] = "HTML"
            media_array.append(item)
            fh = open(path, "rb")
            file_handles.append(fh)
            files[field] = (f"{field}.{ext}", fh, ct)

        return tg_upload("sendMediaGroup", files=files,
                         data_fields={"chat_id": str(chat_id), "media": json.dumps(media_array)})
    finally:
        for fh in file_handles:
            try: fh.close()
            except: pass
        for path, *_ in tmp_paths:
            if os.path.exists(path):
                os.remove(path)


def split_text(text, max_len):
    """Split text at newline boundaries."""
    parts = []
    while len(text) > max_len:
        pos = text.rfind("\n", 0, max_len)
        if pos == -1:
            pos = max_len
        parts.append(text[:pos])
        text = text[pos:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


# ============================================================
# Channel Scraper (پیشرفته)
# ============================================================


def scrape_channel(channel_name):
    """Scrape recent posts from a public Telegram channel."""
    url = f"https://t.me/s/{channel_name}"
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[SCRAPE ERROR] {channel_name}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    posts = []
    now = datetime.utcnow()

    for msg in soup.select(".tgme_widget_message_wrap"):
        try:
            link_el = msg.select_one(".tgme_widget_message")
            if not link_el:
                continue
            data_post = link_el.get("data-post", "")
            post_id = data_post.split("/")[-1] if "/" in data_post else data_post
            if not post_id:
                continue

            # فیلتر زمانی — فقط ۲۴ ساعت اخیر
            date_el = msg.select_one(".tgme_widget_message_date time")
            date_str = date_el.get("datetime", "") if date_el else ""
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    if now - dt > timedelta(hours=MAX_POST_AGE_HOURS):
                        continue
                except:
                    pass

            # متن
            text_el = msg.select_one(".tgme_widget_message_text")
            text = ""
            if text_el:
                for br in text_el.find_all("br"):
                    br.replace_with("\n")
                text = text_el.get_text(strip=False).strip()

            # Helper: extract URL from CSS background-image
            def _bg_url(style):
                for quote in ("url('", 'url("'):
                    idx = style.find(quote)
                    if idx != -1:
                        start = idx + len(quote)
                        end = style.find(quote[-1] + ")", start)
                        if end > start:
                            return style[start:end]
                return None

            seen_urls = set()

            # عکس‌ها
            photos = []
            for wrap in msg.select(".tgme_widget_message_photo_wrap"):
                u = _bg_url(wrap.get("style", ""))
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    photos.append(u)

            # ویدیوها
            videos = []
            for video_el in msg.select("video"):
                src = video_el.get("src", "") or video_el.get("data-src", "")
                if src and src not in seen_urls:
                    seen_urls.add(src)
                    videos.append(src)
                for source in video_el.select("source[src]"):
                    s = source["src"]
                    if s and s not in seen_urls:
                        seen_urls.add(s)
                        videos.append(s)

            for vw in msg.select(".tgme_widget_message_video_wrap"):
                src = vw.get("data-src", "") or vw.get("data-url", "")
                if src and src not in seen_urls:
                    seen_urls.add(src)
                    videos.append(src)

            # بازدید
            views_el = msg.select_one(".tgme_widget_message_views")
            views = views_el.get_text(strip=True) if views_el else ""

            posts.append({
                "id": post_id,
                "channel": channel_name,
                "text": text,
                "photos": photos,
                "videos": videos,
                "date": date_str,
                "views": views,
                "link": f"https://t.me/{channel_name}/{post_id}",
            })

        except Exception as e:
            print(f"[PARSE ERROR] {channel_name}: {e}")
            continue

    return posts


def make_content_hash(channel, text, post_id):
    """Create content-based hash for dedup."""
    # Use both post_id and content for robust dedup
    raw = f"{channel}_{post_id}_{re.sub(r's+', '', text[:60])}"
    return hashlib.md5(raw.encode()).hexdigest()


def make_text_hash(text):
    """Create text-only hash for cross-channel dedup (same news from multiple channels)."""
    cleaned = re.sub(r'\s+', '', text[:200]).strip()
    return hashlib.md5(f"txt_{cleaned}".encode()).hexdigest()


def format_post(post, province_label, for_caption=False):
    """Format a scraped post for forwarding."""
    parts = []
    parts.append(f"📌 <b>{province_label}</b> — @{post['channel']}")

    if post.get("date"):
        try:
            dt = datetime.fromisoformat(post["date"].replace("+00:00", "+00:00"))
            parts.append(f"🕐 {dt.strftime('%Y-%m-%d %H:%M')}")
        except:
            pass

    parts.append("")

    if post.get("text"):
        text = post["text"]
        if for_caption and len(text) > 800:
            text = text[:800] + "..."
        parts.append(text)

    parts.append("")
    parts.append(f'🔗 <a href="{post["link"]}">منبع خبر</a>')
    return "\n".join(parts)


def forward_post(post, channel_id, province_label):
    """Forward a post to a channel with media support."""
    photos = post.get("photos", [])
    videos = post.get("videos", [])
    caption = format_post(post, province_label, for_caption=True)
    formatted = format_post(post, province_label)

    # Strategy: mixed > album > single photo > single video > text
    if photos and videos:
        result = send_media_group_upload(channel_id, photos, caption, video_urls=videos)
        if result.get("ok"):
            return True
        # Fallback to text
        result = send_message(channel_id, formatted + "\n\n🖼🎥 <i>رسانه در لینک اصلی</i>")
        return result.get("ok", False)

    elif photos:
        if len(photos) > 1:
            result = send_media_group_upload(channel_id, photos, caption)
        else:
            result = send_photo_upload(channel_id, photos[0], caption)
        if result.get("ok"):
            return True
        result = send_message(channel_id, formatted + "\n\n🖼 <i>عکس در لینک اصلی</i>")
        return result.get("ok", False)

    elif videos:
        if len(videos) > 1:
            result = send_media_group_upload(channel_id, [], caption, video_urls=videos)
        else:
            result = send_video_upload(channel_id, videos[0], caption)
        if result.get("ok"):
            return True
        result = send_message(channel_id, formatted + "\n\n🎥 <i>ویدیو در لینک اصلی</i>")
        return result.get("ok", False)

    else:
        if formatted.strip():
            result = send_message(channel_id, formatted)
            return result.get("ok", False)
        return True  # Skip empty


# ============================================================
# Province Detection (keyword filter for national sources)
# ============================================================


def detect_provinces(text):
    """Detect which provinces a text is about based on keywords.
    Returns list of province IDs, e.g. ["fars"], ["hormozgan"], or ["fars", "hormozgan"].
    """
    if not text:
        return []
    matched = []
    for pid, keywords in PROVINCE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            matched.append(pid)
    return matched


# ============================================================
# Main Scraper Loop
# ============================================================


def check_all_channels():
    """Check all province sources for new posts."""
    provinces = get_provinces()
    paused = get_setting("paused", "0") == "1"
    if paused:
        print("[SCRAPER] Paused — skipping.")
        return

    total_new = 0

    for pid, pdata in provinces.items():
        channel_id = pdata["channel"]
        label = pdata["label"]

        if not channel_id:
            print(f"[SCRAPER] No channel set for {pid}, skipping.")
            continue

        for source in pdata["sources"]:
            print(f"[CHECK] {label}: @{source}...")
            posts = scrape_channel(source)

            if not posts:
                continue

            for post in posts:
                content_hash = make_content_hash(source, post.get("text", ""), post["id"])

                if is_seen(content_hash):
                    continue

                # بررسی کلمات کلیدی استان — فقط پست‌هایی که مربوط به این استان هستند
                text = post.get("text", "")
                matched = detect_provinces(text)
                if not matched or pid not in matched:
                    mark_seen(content_hash, pid, source, post["id"])
                    continue

                # Keyword filter (user-defined)
                filters = get_setting("filters", "").strip()
                if filters:
                    filter_list = [f.strip().lower() for f in filters.split(",") if f.strip()]
                    post_text = text.lower()
                    if filter_list and post_text and not any(kw in post_text for kw in filter_list):
                        mark_seen(content_hash, pid, source, post["id"])
                        continue

                # Cross-channel dedup: skip if same text already sent to this province
                txt_hash = f"{pid}_{make_text_hash(text)}"
                if is_seen(txt_hash):
                    mark_seen(content_hash, pid, source, post["id"])
                    continue

                print(f"[NEW] {label}/@{source}/{post['id']}: {text[:50]}...")
                success = forward_post(post, channel_id, label)

                if success:
                    mark_seen(content_hash, pid, source, post["id"])
                    mark_seen(txt_hash, pid, source, post["id"])
                    total_new += 1
                    time.sleep(1.5)
                else:
                    print(f"[ERROR] Failed: @{source}/{post['id']}")

    print(f"[DONE] {total_new} new posts forwarded.")

    # Periodic cleanup
    cleanup_old_records(days=7)


def check_national_channels():
    """Check national sources and route to provinces by keyword."""
    provinces = get_provinces()
    paused = get_setting("paused", "0") == "1"
    if paused:
        return

    total_new = 0

    for source in NATIONAL_SOURCES:
        print(f"[NATIONAL] @{source}...")
        posts = scrape_channel(source)

        if not posts:
            continue

        for post in posts:
            text = post.get("text", "")
            matched_provinces = detect_provinces(text)

            if not matched_provinces:
                # No province match — skip
                continue

            base_hash = make_content_hash(source, text, post["id"])

            for pid in matched_provinces:
                if pid not in provinces:
                    continue
                pdata = provinces[pid]
                channel_id = pdata["channel"]
                if not channel_id:
                    continue

                # Province-specific hash so same post can go to both channels
                nat_hash = f"nat_{pid}_{base_hash}"

                if is_seen(nat_hash):
                    continue

                # Cross-channel dedup: skip if same text already sent to this province
                txt_hash = f"{pid}_{make_text_hash(text)}"
                if is_seen(txt_hash):
                    mark_seen(nat_hash, pid, source, post["id"])
                    continue

                label = pdata["label"]
                print(f"[NATIONAL→{label}] @{source}/{post['id']}: {text[:50]}...")
                success = forward_post(post, channel_id, f"{label} (ملی)")

                if success:
                    mark_seen(nat_hash, pid, source, post["id"])
                    mark_seen(txt_hash, pid, source, post["id"])
                    total_new += 1
                    time.sleep(1.5)
                else:
                    print(f"[ERROR] Failed national: @{source}/{post['id']}")

    if total_new:
        print(f"[NATIONAL] {total_new} new posts routed.")


def check_national_web_sources():
    """Check national web/RSS sources and route by keyword."""
    provinces = get_provinces()
    if get_setting("web_paused", "0") == "1":
        return

    total_new = 0

    for ws in NATIONAL_WEB_SOURCES:
        feed_url = ws["feed_url"]
        name = ws["name"]
        print(f"[NATIONAL WEB] {name}...")

        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                continue
        except:
            continue

        for entry in feed.entries:
            link = entry.get("link", "")
            if not link:
                continue

            title = strip_html(entry.get("title", "")).strip()
            description = strip_html(entry.get("summary", "") or entry.get("description", ""))
            full_text = f"{title} {description}"

            matched_provinces = detect_provinces(full_text)
            if not matched_provinces:
                continue

            for pid in matched_provinces:
                if pid not in provinces:
                    continue
                pdata = provinces[pid]
                channel_id = pdata["channel"]
                if not channel_id:
                    continue
                label = pdata["label"]

                nat_url = f"nat_{pid}_{link}"
                if is_web_seen(nat_url):
                    continue

                pub_date = ""
                if hasattr(entry, "published"):
                    try:
                        import email.utils
                        dt = email.utils.parsedate_to_datetime(entry.published)
                        pub_date = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        pub_date = entry.get("published", "")[:16]

                caption = format_web_post(f"{label} (ملی)", name, title,
                                          entry.get("summary", ""), pub_date, link)

                img_url = find_article_image(link)
                sent = False
                if img_url:
                    result = send_photo_upload(channel_id, img_url, caption)
                    if result.get("ok"):
                        sent = True
                if not sent:
                    result = send_message(channel_id, caption, disable_preview=False)
                    sent = result.get("ok", False)

                if sent:
                    mark_web_seen(nat_url, pid, name)
                    total_new += 1
                    time.sleep(2)

    if total_new:
        print(f"[NATIONAL WEB] {total_new} articles routed.")


def scraper_loop():
    """Background loop for channel checking."""
    print("[SCRAPER] Starting...")
    time.sleep(5)

    while True:
        try:
            check_all_channels()
            check_national_channels()
        except Exception as e:
            print(f"[SCRAPER ERROR] {e}")
            traceback.print_exc()

        interval = int(get_setting("check_interval", str(CHECK_INTERVAL_DEFAULT)))
        print(f"[SCRAPER] Next in {interval}s ({interval // 60}m)...")
        time.sleep(interval)


# ============================================================
# Web RSS Scraper
# ============================================================


def strip_html(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def find_article_image(url):
    """Try to find og:image for an article."""
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og and og.get("content"):
            return og["content"]
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw and tw.get("content"):
            return tw["content"]
    except:
        pass
    return None


def format_web_post(province_label, name, title, description, pub_date, link):
    """Format an RSS article."""
    parts = [f"🌐 <b>{province_label}</b> — {name}"]
    if pub_date:
        parts.append(f"🕐 {pub_date}")
    parts.append("")
    parts.append(f"<b>{title}</b>")
    desc = strip_html(description)
    if desc:
        parts.append(desc[:200] + ("..." if len(desc) > 200 else ""))
    parts.append("")
    parts.append(f'🔗 <a href="{link}">ادامه خبر</a>')
    return "\n".join(parts)


def check_all_web_sources():
    """Check all web RSS sources for new articles."""
    if not _web_lock.acquire(blocking=False):
        return

    try:
        if get_setting("web_paused", "0") == "1":
            print("[WEB] Paused.")
            return

        provinces = get_provinces()
        total_new = 0

        for pid, pdata in provinces.items():
            channel_id = pdata["channel"]
            label = pdata["label"]

            if not channel_id:
                continue

            for ws in pdata.get("web_sources", []):
                feed_url = ws.get("feed_url", "")
                name = ws.get("name", feed_url)
                if not feed_url:
                    continue

                print(f"[WEB] {label}: {name}...")
                try:
                    feed = feedparser.parse(feed_url)
                    if feed.bozo and not feed.entries:
                        continue
                except:
                    continue

                for entry in feed.entries:
                    link = entry.get("link", "")
                    if not link or is_web_seen(link):
                        continue

                    title = strip_html(entry.get("title", "")).strip()
                    description = entry.get("summary", "") or entry.get("description", "")
                    pub_date = ""
                    if hasattr(entry, "published"):
                        try:
                            import email.utils
                            dt = email.utils.parsedate_to_datetime(entry.published)
                            pub_date = dt.strftime("%Y-%m-%d %H:%M")
                        except:
                            pub_date = entry.get("published", "")[:16]

                    caption = format_web_post(label, name, title, description, pub_date, link)

                    img_url = find_article_image(link)
                    sent = False
                    if img_url:
                        result = send_photo_upload(channel_id, img_url, caption)
                        if result.get("ok"):
                            sent = True

                    if not sent:
                        result = send_message(channel_id, caption, disable_preview=False)
                        sent = result.get("ok", False)

                    if sent:
                        mark_web_seen(link, pid, name)
                        total_new += 1
                        time.sleep(2)

        print(f"[WEB] {total_new} new articles forwarded.")
    finally:
        _web_lock.release()


def web_scraper_loop():
    """Background loop for RSS checking."""
    print("[WEB] Starting...")
    time.sleep(10)

    while True:
        try:
            check_all_web_sources()
            check_national_web_sources()
        except Exception as e:
            print(f"[WEB ERROR] {e}")
            traceback.print_exc()
        time.sleep(WEB_CHECK_INTERVAL)


# ============================================================
# Bot Commands
# ============================================================


def handle_commands():
    """Poll for bot commands."""
    print("[BOT] Starting command handler...")
    offset = int(get_setting("last_update_id", "0"))

    while True:
        try:
            result = tg_request("getUpdates", offset=offset + 1, timeout=30)
            if not result.get("ok"):
                time.sleep(5)
                continue

            for update in result.get("result", []):
                offset = update["update_id"]
                set_setting("last_update_id", str(offset))

                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id")
                user_id = str(msg.get("from", {}).get("id", ""))

                if not text or not chat_id:
                    continue

                if ADMIN_USER_ID and user_id != ADMIN_USER_ID:
                    send_message(chat_id, "⛔ شما اجازه استفاده از این بات را ندارید.")
                    continue

                # Route commands
                if text.startswith("/add "):
                    cmd_add(chat_id, text)
                elif text.startswith("/remove ") or text.startswith("/delete "):
                    cmd_remove(chat_id, text)
                elif text == "/list":
                    cmd_list(chat_id)
                elif text == "/status":
                    cmd_status(chat_id)
                elif text == "/check":
                    cmd_check(chat_id)
                elif text.startswith("/interval"):
                    cmd_interval(chat_id, text)
                elif text == "/pause":
                    cmd_pause(chat_id)
                elif text == "/resume":
                    cmd_resume(chat_id)
                elif text.startswith("/addweb "):
                    cmd_addweb(chat_id, text)
                elif text.startswith("/removeweb "):
                    cmd_removeweb(chat_id, text)
                elif text == "/listweb":
                    cmd_listweb(chat_id)
                elif text == "/checkweb":
                    cmd_checkweb(chat_id)
                elif text == "/pauseweb":
                    set_setting("web_paused", "1")
                    send_message(chat_id, "⏸ اسکنر وب متوقف شد.\nبرای ادامه: /resumeweb")
                elif text == "/resumeweb":
                    set_setting("web_paused", "0")
                    send_message(chat_id, "▶️ اسکنر وب فعال شد.")
                elif text.startswith("/filter"):
                    cmd_filter(chat_id, text)
                elif text in ("/start", "/help"):
                    cmd_help(chat_id)
                else:
                    send_message(chat_id, "❓ دستور نامعتبر. /help بزنید.")

        except Exception as e:
            print(f"[BOT ERROR] {e}")
            traceback.print_exc()
            time.sleep(5)


# ---- Command Implementations ----


def cmd_add(chat_id, text):
    """/add <province> <channel> — Add a source to a province."""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        provinces = get_provinces()
        plist = " / ".join(provinces.keys())
        send_message(chat_id,
                     f"⚠️ <b>استفاده:</b>\n<code>/add &lt;استان&gt; &lt;کانال&gt;</code>\n\n"
                     f"استان‌ها: <code>{plist}</code>\n"
                     f"مثال: <code>/add fars shiraz_news</code>\n"
                     f"مثال: <code>/add hormozgan bandar_news</code>")
        return

    province = parts[1].strip().lower()
    channel = parts[2].strip().lstrip("@").replace("https://t.me/", "").strip("/")

    provinces = get_provinces()
    if province not in provinces:
        send_message(chat_id, f"⚠️ استان <code>{province}</code> وجود ندارد.\nاستان‌ها: {', '.join(provinces.keys())}")
        return

    if channel in provinces[province]["sources"]:
        send_message(chat_id, f"ℹ️ @{channel} قبلاً در {provinces[province]['label']} هست.")
        return

    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO province_sources (province_id, source) VALUES (?, ?)", (province, channel))
    conn.commit()
    conn.close()

    send_message(chat_id, f"✅ @{channel} به <b>{provinces[province]['label']}</b> اضافه شد!")


def cmd_remove(chat_id, text):
    """/remove <province> <channel>"""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        send_message(chat_id,
                     "⚠️ <b>استفاده:</b>\n<code>/remove &lt;استان&gt; &lt;کانال&gt;</code>\n"
                     "مثال: <code>/remove fars shiraz_news</code>")
        return

    province = parts[1].strip().lower()
    channel = parts[2].strip().lstrip("@")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM province_sources WHERE province_id = ? AND source = ?", (province, channel))
    if cur.rowcount > 0:
        conn.commit()
        send_message(chat_id, f"🗑 @{channel} از {province} حذف شد.")
    else:
        send_message(chat_id, f"⚠️ @{channel} در {province} پیدا نشد.")
    conn.close()


def cmd_list(chat_id):
    """/list — Show all provinces and sources."""
    provinces = get_provinces()
    lines = ["📋 <b>لیست منابع:</b>\n"]
    for pid, pdata in provinces.items():
        lines.append(f"🏷 <b>{pdata['label']}</b> ({pid}):")
        lines.append(f"   📤 کانال: <code>{pdata['channel']}</code>")
        if pdata["sources"]:
            for i, s in enumerate(pdata["sources"], 1):
                lines.append(f"   {i}. @{s}")
        else:
            lines.append("   (خالی)")
        lines.append("")
    send_message(chat_id, "\n".join(lines))


def cmd_status(chat_id):
    """/status"""
    provinces = get_provinces()
    paused = get_setting("paused", "0") == "1"
    web_paused = get_setting("web_paused", "0") == "1"
    interval = int(get_setting("check_interval", str(CHECK_INTERVAL_DEFAULT)))
    filters = get_setting("filters", "")

    conn = get_db()
    total_posts = conn.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0]
    total_web = conn.execute("SELECT COUNT(*) FROM seen_web").fetchone()[0]
    conn.close()

    lines = [
        "📊 <b>وضعیت NABZ:</b>\n",
        f"🔘 اسکنر کانال: {'⏸ متوقف' if paused else '▶️ فعال'}",
        f"🌐 اسکنر وب: {'⏸ متوقف' if web_paused else '▶️ فعال'}",
        f"⏱ بررسی هر {interval // 60} دقیقه",
        f"🔍 فیلتر: {filters if filters else 'غیرفعال'}",
        ""
    ]

    for pid, pdata in provinces.items():
        lines.append(f"🏷 <b>{pdata['label']}</b>: {len(pdata['sources'])} منبع TG + {len(pdata.get('web_sources', []))} منبع وب")

    lines.append(f"\n📨 پست‌ها: {total_posts} | مقالات: {total_web}")
    send_message(chat_id, "\n".join(lines))


def cmd_check(chat_id):
    """/check — Force immediate check."""
    send_message(chat_id, "🔄 بررسی فوری...")
    try:
        check_all_channels()
        send_message(chat_id, "✅ بررسی کامل شد!")
    except Exception as e:
        send_message(chat_id, f"❌ خطا: {e}")


def cmd_interval(chat_id, text):
    """/interval [minutes]"""
    parts = text.split(maxsplit=1)
    current = int(get_setting("check_interval", str(CHECK_INTERVAL_DEFAULT)))

    if len(parts) < 2:
        send_message(chat_id, f"⏱ فاصله فعلی: {current // 60} دقیقه\nتغییر: <code>/interval 5</code>")
        return

    try:
        minutes = int(parts[1].strip())
    except ValueError:
        send_message(chat_id, "⚠️ عدد وارد کنید.")
        return

    if minutes < 1 or minutes > 1440:
        send_message(chat_id, "⚠️ بین ۱ تا ۱۴۴۰ دقیقه.")
        return

    set_setting("check_interval", str(minutes * 60))
    send_message(chat_id, f"✅ فاصله به {minutes} دقیقه تغییر یافت.")


def cmd_pause(chat_id):
    set_setting("paused", "1")
    send_message(chat_id, "⏸ فوروارد کانال‌ها متوقف شد.\nادامه: /resume")


def cmd_addweb(chat_id, text):
    """/addweb <province> <url>"""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        provinces = get_provinces()
        plist = " / ".join(provinces.keys())
        send_message(chat_id,
                     f"⚠️ <b>استفاده:</b>\n<code>/addweb &lt;استان&gt; &lt;URL&gt;</code>\n\n"
                     f"استان‌ها: <code>{plist}</code>\n"
                     f"مثال: <code>/addweb fars https://iranfocus.com</code>")
        return

    province = parts[1].strip().lower()
    base_url = parts[2].strip().rstrip("/")
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    provinces = get_provinces()
    if province not in provinces:
        send_message(chat_id, f"⚠️ استان {province} وجود ندارد.")
        return

    send_message(chat_id, f"🔍 جستجوی RSS برای {base_url}...")

    candidates = [base_url + s for s in ["/feed/", "/rss/", "/rss.xml", "/atom.xml"]] + [base_url]
    found_feed = None
    for c in candidates:
        try:
            feed = feedparser.parse(c)
            if feed.entries:
                found_feed = c
                break
        except:
            continue

    if not found_feed:
        send_message(chat_id, "⚠️ فید RSS پیدا نشد.")
        return

    feed = feedparser.parse(found_feed)
    name = strip_html(feed.feed.get("title", ""))[:40] or base_url.split("//")[-1].split("/")[0]

    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO province_web_sources (province_id, feed_url, name, site_url) VALUES (?, ?, ?, ?)",
        (province, found_feed, name, base_url)
    )
    conn.commit()
    conn.close()

    send_message(chat_id, f"✅ {name} به <b>{provinces[province]['label']}</b> اضافه شد!\n📡 {found_feed}")


def cmd_removeweb(chat_id, text):
    """/removeweb <province> <name>"""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        send_message(chat_id, "⚠️ <code>/removeweb &lt;استان&gt; &lt;نام&gt;</code>")
        return
    province = parts[1].strip().lower()
    query = parts[2].strip().lower()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM province_web_sources WHERE province_id = ? AND LOWER(name) LIKE ?",
                (province, f"%{query}%"))
    if cur.rowcount > 0:
        conn.commit()
        send_message(chat_id, f"🗑 حذف شد.")
    else:
        send_message(chat_id, "⚠️ پیدا نشد.")
    conn.close()


def cmd_listweb(chat_id):
    provinces = get_provinces()
    lines = ["🌐 <b>منابع وب:</b>\n"]
    for pid, pdata in provinces.items():
        lines.append(f"🏷 <b>{pdata['label']}</b>:")
        ws_list = pdata.get("web_sources", [])
        if ws_list:
            for i, ws in enumerate(ws_list, 1):
                lines.append(f"   {i}. {ws['name']}")
                lines.append(f"      <code>{ws['feed_url']}</code>")
        else:
            lines.append("   (خالی)")
        lines.append("")
    send_message(chat_id, "\n".join(lines))


def cmd_checkweb(chat_id):
    send_message(chat_id, "🔄 بررسی فوری وب...")
    try:
        check_all_web_sources()
        send_message(chat_id, "✅ بررسی وب کامل شد!")
    except Exception as e:
        send_message(chat_id, f"❌ خطا: {e}")


def cmd_filter(chat_id, text):
    parts = text.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) > 1 else "list"
    filters = get_setting("filters", "")
    filter_list = [f.strip() for f in filters.split(",") if f.strip()] if filters else []

    if sub == "list" or sub == "/filter":
        if not filter_list:
            send_message(chat_id, "🔍 فیلتر: غیرفعال\nهمه پست‌ها ارسال می‌شوند.\n\n<code>/filter add کلمه</code>")
        else:
            kws = "\n".join(f"  {i+1}. <code>{k}</code>" for i, k in enumerate(filter_list))
            send_message(chat_id, f"🔍 <b>فیلترها ({len(filter_list)}):</b>\n{kws}\n\n/filter clear — حذف همه")
    elif sub == "add" and len(parts) > 2:
        kw = parts[2].strip().lower()
        if kw not in [f.lower() for f in filter_list]:
            filter_list.append(kw)
            set_setting("filters", ",".join(filter_list))
            send_message(chat_id, f"✅ فیلتر <code>{kw}</code> اضافه شد.")
        else:
            send_message(chat_id, f"ℹ️ قبلاً هست.")
    elif sub == "remove" and len(parts) > 2:
        kw = parts[2].strip().lower()
        filter_list = [f for f in filter_list if f.lower() != kw]
        set_setting("filters", ",".join(filter_list))
        send_message(chat_id, f"🗑 حذف شد.")
    elif sub == "clear":
        set_setting("filters", "")
        send_message(chat_id, "🗑 همه فیلترها حذف شدند.")
    else:
        send_message(chat_id, "/filter list | /filter add <code>کلمه</code> | /filter clear")


def cmd_help(chat_id):
    help_text = """🤖 <b>NABZ — نبض خبری</b>

جمع‌آوری اخبار از کانال‌های تلگرام + سایت‌ها و ارسال به کانال‌های استانی.

<b>📡 مدیریت منابع تلگرام:</b>
<code>/add fars channel</code> — اضافه به فارس
<code>/add hormozgan channel</code> — اضافه به هرمزگان
<code>/remove fars channel</code> — حذف منبع
<code>/list</code> — نمایش همه منابع

<b>🌐 منابع وب (RSS):</b>
<code>/addweb fars https://site.com</code>
<code>/removeweb fars نام</code>
<code>/listweb</code>
<code>/checkweb</code> — بررسی فوری
<code>/pauseweb</code> / <code>/resumeweb</code>

<b>⚙️ تنظیمات:</b>
<code>/status</code> — وضعیت بات
<code>/check</code> — بررسی فوری کانال‌ها
<code>/interval 5</code> — فاصله بررسی (دقیقه)
<code>/pause</code> / <code>/resume</code>

<b>🔍 فیلتر کلمه‌ای:</b>
<code>/filter add کلمه</code>
<code>/filter remove کلمه</code>
<code>/filter clear</code>
<code>/filter list</code>

💡 فقط کانال‌های عمومی قابل اسکرپ هستند."""
    send_message(chat_id, help_text)


# ============================================================
# Flask
# ============================================================


@app.route("/")
def home():
    provinces = get_provinces()
    total_sources = sum(len(p["sources"]) for p in provinces.values())
    return f"""
    <html dir="rtl">
    <head><title>NABZ — نبض خبری</title></head>
    <body style="font-family: Tahoma; text-align: center; padding: 50px;">
        <h1>🤖 NABZ — نبض خبری</h1>
        <p>✅ بات فعال است</p>
        <p>📡 منابع: {total_sources} کانال</p>
        <p>🏷 استان‌ها: {', '.join(p['label'] for p in provinces.values())}</p>
        <p>⏱ {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}</p>
    </body>
    </html>
    """


@app.route("/check")
def check_endpoint():
    """Endpoint for cron-job.org to trigger checks."""
    threading.Thread(target=check_all_channels, daemon=True).start()
    threading.Thread(target=check_all_web_sources, daemon=True).start()
    return "OK"


# ============================================================
# Initialization — runs on import (Render/gunicorn compatible)
# ============================================================

_initialized = False
_init_lock = threading.Lock()


def ensure_init():
    """Initialize DB and start background threads (once)."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return

        if not BOT_TOKEN:
            print("⚠️ TELEGRAM_BOT_TOKEN not set!")
            return

        # Initialize database
        init_db()

        print("=" * 50)
        print("🤖 NABZ — نبض خبری")
        print("=" * 50)

        provinces = get_provinces()
        for pid, pdata in provinces.items():
            print(f"🏷 {pdata['label']}: {len(pdata['sources'])} sources → {pdata['channel']}")

        print(f"📡 ملی: {len(NATIONAL_SOURCES)} کانال TG + {len(NATIONAL_WEB_SOURCES)} وب (فیلتر کلمه‌کلیدی)")
        print(f"👤 Admin: {ADMIN_USER_ID or 'Not set'}")
        print(f"⏱ هر {CHECK_INTERVAL_DEFAULT // 60} دقیقه")
        print("=" * 50)

        # Background threads
        threading.Thread(target=scraper_loop, daemon=True).start()
        threading.Thread(target=web_scraper_loop, daemon=True).start()
        threading.Thread(target=handle_commands, daemon=True).start()

        _initialized = True
        print("✅ All systems started!")


# Auto-initialize on import (for gunicorn / Render)
ensure_init()


# ============================================================
# Main — for direct execution (Replit / local)
# ============================================================


def main():
    ensure_init()

    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set! Add to Secrets/Environment.")
        return
    if not FARS_CHANNEL and not BND_CHANNEL:
        print("❌ At least one channel ID needed! Set FARS_CHANNEL_ID or BND_CHANNEL_ID.")
        return

    # Flask (main thread)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
