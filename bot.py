import os
import sqlite3
import feedparser
import requests

# ==========================================
# 1. إعداد المفاتيح
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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
    conn = sqlite3.connect("sent_news.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def is_already_sent(article_id):
    conn = sqlite3.connect("sent_news.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM articles WHERE id = ?", (article_id,))
    data = cursor.fetchone()
    conn.close()
    return data is not None

def mark_as_sent(article_id):
    conn = sqlite3.connect("sent_news.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO articles (id) VALUES (?)", (article_id,))
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
    التفاصيل: {summary}

    المطلوب:
    - إذا كان الخبر لا يتعلق بتونس إطلاقاً (أخبار دولية بحتة عن أمريكا، روسيا، السعودية، إيران، إلخ): أجب بكلمة واحدة فقط: تجاهل
    - إذا كان الخبر يخص تونس أو الشأن التونسي الداخلي: اكتب ملخصاً إخبارياً واضحاً في سطرين. إياك أن تكتب كلمة تجاهل في الملخص.
    """
    
    # الحل النهائي: استخدام أحدث نسخة مدعومة للجميع (gemini-1.5-flash-latest) على مسار v1beta
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        
        if "candidates" in data:
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            print(f"      [رد الذكاء الاصطناعي]: {text[:60]}...")
            
            # فحص الرد العربي
            cleaned = text.replace(".", "").strip()
            if cleaned == "تجاهل" or (cleaned.startswith("تجاهل") and len(cleaned) <= 10):
                return None
                
            return text
        else:
            print(f"      [خطأ في الاستجابة]: {data}")
            return None
    except Exception as e:
        print(f"Error calling AI API: {e}")
        return None

# ==========================================
# 5. الإرسال إلى تيليجرام
# ==========================================
def send_telegram(text, link):
    message = f"🚨 **خبر جديد** 🚨\n\n{text}\n\n🔗 رابط المصدر: {link}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"      [خطأ تيليجرام]: {response.json()}")
    except Exception as e:
        print(f"Error sending to Telegram: {e}")

# ==========================================
# 6. التشغيل الرئيسي
# ==========================================
def main():
    init_db()
    print("بدأ البحث عن الأخبار في المصادر المحددة...")
    
    for feed_url in FEEDS:
        if feed_url.strip().startswith("#"):
            continue
            
        print(f"\nجاري فحص المصدر: {feed_url}")
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:5]:  
                entry_id = entry.get("id", entry.get("link"))
                title = entry.get("title", "")
                
                if is_already_sent(entry_id):
                    print(f"[-] تم تخطي (خبر قديم): {title[:50]}...")
                    continue

                print(f"[+] تحليل خبر جديد: {title}")
                summary = entry.get("summary", "")
                link = entry.get("link", "")

                ai_summary = evaluate_and_summarize(title, summary)
                
                if ai_summary:
                    send_telegram(ai_summary, link)
                    print("   -> 🟢 تم إرسال الخبر للتيليجرام!")
                else:
                    print("   -> 🔴 تم التجاهل من الذكاء الاصطناعي (لا يخص تونس).")
                
                mark_as_sent(entry_id)
                
        except Exception as e:
            print(f"حدث خطأ أثناء قراءة المصدر {feed_url}: {e}")
                
    print("\nانتهت العملية بنجاح.")

if __name__ == "__main__":
    main()
