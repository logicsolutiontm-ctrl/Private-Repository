import os
import sys
import time
import sqlite3
import feedparser
import requests

# طباعة فورية في سجلات GitHub Actions
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ==========================================
# 1. الإعدادات والمفاتيح
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FRIEND_CHAT_ID = os.getenv("FRIEND_CHAT_ID")

# نماذج 1.5 أُوقفت (404). نستخدم 2.5، ويمكن تغيير الأول عبر GEMINI_MODEL
MODELS = list(dict.fromkeys(
    m for m in [
        os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ] if m
))

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
DB_FILE = "sent_news.db"

MAX_ENTRIES_PER_FEED = 5      # عدد الأخبار المفحوصة من كل مصدر
DELAY_BETWEEN_AI_CALLS = 6    # ثوانٍ بين كل طلب Gemini (لتفادي 429)
MAX_RETRY_WAIT = 30           # أقصى انتظار مقبول عند 429 قبل تجربة نموذج آخر
RUN_TIME_BUDGET = 12 * 60     # أقصى مدة تشغيل بالثواني (12 دقيقة)

START_TIME = time.time()

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
# 3. قاعدة البيانات
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
DEAD_MODELS = set()          # نماذج أعطت 404
RATE_LIMITED = set()         # نماذج تجاوزت الحد في هذا التشغيل
_discovery_done = False


def discover_models():
    """يجلب النماذج المتاحة فعلياً لهذا المفتاح (يُستدعى فقط إذا ماتت كل النماذج بـ 404)."""
    global _discovery_done
    _discovery_done = True
    try:
        r = requests.get(
            f"{GEMINI_BASE}/models",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            params={"pageSize": 200},
            timeout=20,
        )
        r.raise_for_status()
        excluded = ("image", "tts", "live", "audio", "exp", "vision",
                    "embedding", "robotics", "computer")
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
        found.sort(reverse=True)
        for name in found[:4]:
            if name not in MODELS:
                MODELS.append(name)
        print(f"      [نماذج مكتشفة تلقائياً]: {found[:4]}")
    except Exception as e:
        print(f"      [فشل اكتشاف النماذج]: {e}")


def extract_text(data):
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError):
        return ""


def build_prompt(title, summary):
    return f"""
أنت محرر صحفي في وكالة أنباء تونسية.
الخبر:
العنوان: {title}
التفاصيل: {summary[:1500]}

المطلوب:
- إذا كان الخبر لا يتعلق بتونس إطلاقاً (أخبار دولية بحتة عن أمريكا، روسيا، السعودية، إيران، إلخ): أجب بكلمة واحدة فقط: تجاهل
- إذا كان الخبر يخص تونس أو الشأن التونسي الداخلي: اكتب ملخصاً إخبارياً واضحاً في سطرين. إياك أن تكتب كلمة تجاهل في الملخص.
"""


def evaluate_and_summarize(title, summary):
    """
    يرجع أحد القيم:
      ("ok", نص)   -> خبر تونسي مع ملخص
      ("skip", None) -> خبر غير تونسي
      ("quota", None) -> كل النماذج تجاوزت الحد، يجب إيقاف التشغيل
      ("error", None) -> فشل مؤقت، يعاد لاحقاً
    """
    payload = {"contents": [{"parts": [{"text": build_prompt(title, summary)}]}]}
    discovery_tried = False

    while True:
        usable = [m for m in MODELS if m not in DEAD_MODELS and m not in RATE_LIMITED]

        for model in usable:
            url = f"{GEMINI_BASE}/models/{model}:generateContent"

            for attempt in range(2):
                try:
                    response = requests.post(
                        url,
                        headers={"x-goog-api-key": GEMINI_API_KEY},
                        json=payload,
                        timeout=40,
                    )
                    code = response.status_code

                    if code == 404:
                        print(f"      [النموذج غير متاح]: {model}")
                        DEAD_MODELS.add(model)
                        break

                    if code == 429:
                        try:
                            wait = int(float(response.headers.get("Retry-After", 20)))
                        except ValueError:
                            wait = 20
                        if attempt == 0 and wait <= MAX_RETRY_WAIT:
                            print(f"      [429 - انتظار {wait} ثانية] {model}")
                            time.sleep(wait)
                            continue
                        print(f"      [429 - تجاوز الحد] {model}، سيتم تجربة نموذج آخر")
                        RATE_LIMITED.add(model)
                        break

                    if code in (500, 502, 503, 504):
                        print(f"      [{code} - إعادة المحاولة] {model}")
                        time.sleep(5)
                        continue

                    if code in (400, 401, 403):
                        print(f"      [خطأ {code} من Gemini - تحقق من المفتاح]: {response.text[:200]}")
                        return "error", None

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

        # لا يوجد نموذج صالح
        alive = [m for m in MODELS if m not in DEAD_MODELS]
        if not alive and not _discovery_done and not discovery_tried:
            discovery_tried = True
            discover_models()
            continue

        if alive and all(m in RATE_LIMITED for m in alive):
            return "quota", None

        return "error", None

# ==========================================
# 5. الإرسال إلى تيليجرام
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
                print(f"      [خطأ تيليجرام للمعرف {chat_id}]: {response.text[:200]}")
        except Exception as e:
            print(f"      [خطأ إرسال تيليجرام للمعرف {chat_id}]: {e}")

    return success

# ==========================================
# 6. التشغيل الرئيسي
# ==========================================
def time_is_up():
    return (time.time() - START_TIME) > RUN_TIME_BUDGET


def main():
    if not (GEMINI_API_KEY and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        raise SystemExit("مفتاح ناقص: تحقق من GitHub Secrets")

    init_db()
    print("بدأ البحث عن الأخبار في المصادر المحددة...")
    print(f"النماذج المستخدمة: {MODELS}")

    stop = False

    for feed_url in FEEDS:
        if stop:
            break
        if feed_url.strip().startswith("#"):
            continue

        print(f"\nجاري فحص المصدر: {feed_url}")

        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"حدث خطأ أثناء قراءة المصدر {feed_url}: {e}")
            continue

        for entry in feed.entries[:MAX_ENTRIES_PER_FEED]:
            if time_is_up():
                print("\n⏱️ انتهت مهلة التشغيل، سيُكمل في المرة القادمة.")
                stop = True
                break

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

            if status == "ok":
                if send_telegram(ai_summary, link):
                    print("   -> 🟢 تم إرسال الخبر للتيليجرام!")
                    mark_as_sent(entry_id)
                else:
                    print("   -> ⚠️ فشل الإرسال، سيُعاد المحاولة لاحقاً.")
            elif status == "skip":
                print("   -> 🔴 تم التجاهل (لا يخص تونس).")
                mark_as_sent(entry_id)
            elif status == "quota":
                print("   -> ⛔ تم تجاوز حد Gemini لكل النماذج، إيقاف التشغيل (سيُكمل لاحقاً).")
                stop = True
                break
            else:
                print("   -> ⚠️ فشل الذكاء الاصطناعي، سيُعاد المحاولة لاحقاً.")

            time.sleep(DELAY_BETWEEN_AI_CALLS)

    print("\nانتهت العملية.")


if __name__ == "__main__":
    main()
