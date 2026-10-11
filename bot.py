import os
import sys
import time
import sqlite3
import feedparser
import requests

# طباعة فورية في سجلات GitHub Actions (بدون تأخير بسبب buffering)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ==========================================
# 1. إعداد المفاتيح
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FRIEND_CHAT_ID = os.getenv("FRIEND_CHAT_ID")

# نماذج 1.5 تم إيقافها (404). نستخدم نماذج 2.5 ويمكن تغييرها عبر GEMINI_MODEL
MODELS = [
    m for m in [
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "gemini-2.5-flash-lite",
    ] if m
]
MODELS = list(dict.fromkeys(MODELS))  # إزالة التكرار مع الحفاظ على الترتيب

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
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
DEAD_MODELS = set()      # نماذج أعطت 404، لا نعيد تجربتها
_discovered = False      # هل تم جلب النماذج المتاحة تلقائياً؟

def discover_models():
    """يجلب النماذج المتاحة فعلياً لهذا المفتاح (تُستدعى فقط إذا فشلت كل النماذج بـ 404)."""
    global _discovered
    _discovered = True
    try:
        r = requests.get(
            f"{GEMINI_BASE}/models",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            params={"pageSize": 200},
            timeout=20,
        )
        r.raise_for_status()
        excluded = ("image", "tts", "live", "audio", "exp", "vision", "embedding", "robotics", "computer")
        found = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if (
                "generateContent" in m.get("supportedGenerationMethods", [])
                and "flash" in name
                and not any(x in name for x in excluded)
                and name not in DEAD_MODELS
            ):
                found.append(name)
        found.sort(reverse=True)  # الأحدث أولاً غالباً
        for name in found[:4]:
            if name not in MODELS:
                MODELS.append(name)
        print(f"      [نماذج مكتشفة تلقائياً]: {found[:4]}")
    except Exception as e:
        print(f"      [فشل اكتشاف النماذج]: {e}")

def extract_text(data):
    """استخراج النص من رد Gemini بأمان."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError):
        return ""

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

    tried_discovery = False
    while True:
        for model in list(MODELS):
            if model in DEAD_MODELS:
                continue

            url = f"{GEMINI_BASE}/models/{model}:generateContent"
            for attempt in range(3):
                try:
                    response = requests.post(
                        url,
                        headers={"x-goog-api-key": GEMINI_API_KEY},
                        json=payload,
                        timeout=40,
                    )

                    if response.status_code == 404:
                        print(f"      [النموذج غير متاح]: {model}")
                        DEAD_MODELS.add(model)
                        break

                    if response.status_code == 429:
                        print(f"      [429 - انتظار 20 ثانية] {model}")
                        time.sleep(20)
                        continue

                    if response.status_code in (500, 502, 503, 504):
                        print(f"      [{response.status_code} - إعادة المحاولة] {model}")
                        time.sleep(5 * (attempt + 1))
                        continue

                    response.raise_for_status()

                    text = extract_text(response.json())
                    if not text:
                        print(f"      [رد فارغ أو محجوب] {model}")
                        break

                    print(f"      [رد الذكاء الاصطناعي]: {text[:60]}...")

                    cleaned = text.replace(".", "").strip()
                    if cleaned.startswith("تجاهل") and len(cleaned) <= 10:
                        return "skip", None
                    return "ok", text

                except Exception as e:
                    print(f"      [خطأ Gemini {model}]: {e}")
                    break

        # إذا كانت كل النماذج ميتة (404) نحاول اكتشاف نماذج متاحة مرة واحدة فقط
        if all(m in DEAD_MODELS for m in MODELS) and not _discovered and not tried_discovery:
            tried_discovery = True
            discover_models()
            continue
        break

    return "error", None

# ==========================================
# 5. الإرسال إلى تيليجرام (لأكثر من شخص)
# ==========================================
def send_telegram(text, link):
    message = f"🚨 خبر جديد 🚨\n\n{text}\n\n🔗 رابط المصدر: {link}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    chat_ids = [c.strip() for c in [TELEGRAM_CHAT_ID, FRIEND_CHAT_ID] if c and c.strip()]
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
            print(f"      [خطأ إرسال تيليجرام للمعرف {chat_id}]: {e}")

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
