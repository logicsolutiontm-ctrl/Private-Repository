import os
import time
import sqlite3
import feedparser
import requests

# ==========================================
# 1. إعداد المفاتيح
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FRIEND_CHAT_ID = os.getenv("FRIEND_CHAT_ID") # تمت إضافة معرف الصديق هنا

MODELS = [
    os.getenv("GEMINI_MODEL", "gemini-1.5-flash"), # تم تصحيح اسم النموذج الافتراضي
    "gemini-1.5-flash-8b",
    "gemini-1.5-flash-latest",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
DB_FILE = "sent_news.db"

# ==========================================
# 2. مصادر الأخبار
# ==========================================
FEEDS = [
    "https://www.aljazeera.net/feed",
    "https://www.mosaiquefm.net/ar/rss",
    "https://tunisie-telegraph.com/feed/",
    "https://feeds.bbci.co.uk/arabic/rss.xml",
    "https://www.france24.com/ar/rss",
    "https://rss.app/feeds/jaBascGUQX6aVLXX.xml",
]

# ==========================================
# 3. إدارة قاعدة البيانات
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def is_already_sent(article_id):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    conn.close()
    return row is not None

def mark_as_sent(article_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO articles (id) VALUES (?)", (article_id,))
    conn.commit()
    conn.close()

# ==========================================
# 4. التقييم والتلخيص بالذكاء الاصطناعي
# ==========================================
def evaluate_and_summarize(title, summary):
    prompt = f"""
    أنت محرر صحفي في وكالة أنباء تونسية.
    الخبر:
    العنوان: {title}
    التفاصيل: {summary[:1500]}

    المطلوب:
    - إذا كان الخبر لا يتعلق بتونس إطلاقاً (أخبار دولية بحتة عن أمريكا، روسيا، السعودية، إيران، إلخ): أجب بكلمة واحدة فقط: تجاهل
    - إذا كان الخبر يخص تونس أو الشأن التونسي الداخلي: اكتب ملخصاً إخبارياً واضحاً في سطرين. إياك أن تكتب كلمة تجاهل في الملخص.
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):
            try:
                response = requests.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_API_KEY},
                    json=payload,
                    timeout=40,
                )
                if response.status_code == 429:
                    print(f"      [429 - انتظار 20 ثانية] {model}")
                    time.sleep(20)
                    continue
                if response.status_code == 404:
                    print(f"      [النموذج غير متاح]: {model}")
                    break  
                response.raise_for_status()

                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                print(f"      [رد الذكاء الاصطناعي]: {text[:60]}...")

                cleaned = text.replace(".", "").strip()
                if cleaned.startswith("تجاهل") and len(cleaned) <= 10:
                    return "skip", None
                return "ok", text

            except Exception as e:
                print(f"      [خطأ Gemini {model}]: {e}")
                break

    return "error", None

# ==========================================
# 5. الإرسال إلى تيليجرام (محدث لإرسال لأكثر من شخص)
# ==========================================
def send_telegram(text, link):
    message = f"🚨 خبر جديد 🚨\n\n{text}\n\n🔗 رابط المصدر: {link}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # جمع المعرفات المتاحة (معرفك + معرف صديقك إن وجد)
    chat_ids = [id.strip() for id in [TELEGRAM_CHAT_ID, FRIEND_CHAT_ID] if id]
    success = False

    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": message[:4000],
            "disable_web_page_preview": False,
        }
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                success = True
            else:
                print(f"      [خطأ تيليجرام للمعرف {chat_id}]: {response.text}")
        except Exception as e:
            print(f"Error sending to Telegram chat {chat_id}: {e}")
            
    return success

# ==========================================
# 6. التشغيل الرئيسي
# ==========================================
def main():
    if not (GEMINI_API_KEY and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        raise SystemExit("مفتاح ناقص: تحقق من GitHub Secrets")

    init_db()
    print("بدأ البحث عن الأخبار في المصادر المحددة...")

    for feed_url in FEEDS:
        if feed_url.strip().startswith("#"):
            continue

        print(f"\nجاري فحص المصدر: {feed_url}")

        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:5]:
                entry_id = entry.get("id") or entry.get("link")
                title = entry.get("title", "")
                if not entry_id:
                    continue

                if is_already_sent(entry_id):
                    print(f"[-] تم تخطي (خبر قديم): {title[:50]}...")
                    continue

                print(f"[+] تحليل خبر جديد: {title}")
                summary = entry.get("summary", "")
                link = entry.get("link", "")

                status, ai_summary = evaluate_and_summarize(title, summary)
                time.sleep(5) 

                if status == "ok":
                    if send_telegram(ai_summary, link):
                        print("   -> 🟢 تم إرسال الخبر للتيليجرام!")
                        mark_as_sent(entry_id)
                    else:
                        print("   -> ⚠️ فشل الإرسال، سيُعاد المحاولة لاحقاً.")
                elif status == "skip":
                    print("   -> 🔴 تم التجاهل من الذكاء الاصطناعي (لا يخص تونس).")
                    mark_as_sent(entry_id)
                else:
                    print("   -> ⚠️ فشل الذكاء الاصطناعي، سيُعاد المحاولة لاحقاً.")

        except Exception as e:
            print(f"حدث خطأ أثناء قراءة المصدر {feed_url}: {e}")

    print("\nانتهت العملية بنجاح.")

if __name__ == "__main__":
    main()
