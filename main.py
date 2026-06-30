import os, requests, sys, logging
from flask import Flask

# تنظیمات لاگ ساده
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("OSINT_HEALTH_CHECK")

BOT_TOKEN = "8842107952:AAFszVHNfL331IRN1YWIi6hP9QTY4o3vhxk"
# تست فرستادن به یکی از کانال‌های شما (مثلاً کانال هرمزگان)
TEST_CHANNEL = "-1003915149928" 

app = Flask(__name__)

@app.route('/')
def home():
    return "سرویس رندر بیدار است!", 200

@app.route('/check')
def check():
    logger.info("🚀 درخواست /check دریافت شد. در حال ارسال پیام تست به تلگرام...")
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TEST_CHANNEL,
        "text": "🟢 **تست سلامت سیستم**\n\nزنجیره اتصال گیت‌هاب، رندر و کرون‌جاب کاملاً برقرار است و ربات به درستی کار می‌کند!",
        "parse_mode": "Markdown"
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            logger.info("✅ پیام تست با موفقیت به تلگرام ارسال شد!")
            return "پیام با موفقیت ارسال شد! کانال تلگرام را چک کنید.", 200
        else:
            logger.error(f"❌ خطا در ارسال به تلگرام. کد وضعیت: {r.status_code} | پاسخ: {r.text}")
            return f"خطا در تلگرام: {r.text}", 400
    except Exception as e:
        logger.error(f"❌ درخواست به تلگرام با خطا مواجه شد: {e}")
        return f"خطای سرور: {e}", 500

if __name__ == "__main__":
    # رندر پورت را از متغیر محیطی می‌خواند
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
