import os
import sqlite3
import feedparser
import requests

# ==========================================
# 1. إعداد المفاتيح (من GitHub Secrets)
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==========================================
# 2. مصادر الأخبار (RSS Feeds)
# ==========================================
FEEDS = [
    "https://www.aljazeera.net/feed",
    "https://www.mosaiquefm.net/ar/rss",
    "https://tunisie-telegraph.com/feed/",
    "https://feeds.bbci.co.uk/arabic/rss.xml",
    "https://www.france24.com/ar/rss",
    # "https://rss.app/feeds/v1.1/YOUR_LINK_HERE", # روابط فايسبوك
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
# 4. التقييم والتلخيص بالذكاء الاصطناعي (مباشر عبر API)
# ==========================================
def evaluate_and_summarize(title, summary):
    prompt = f"""
    أنت محرر إخباري ذكي متخصص في الشأن التونسي.
    قيم الخبر التالي:
    العنوان: {title}
    التفاصيل: {summary}

    الشروط:
    1. هل الخبر متعلق بتونس (أو له تأثير مباشر عليها) ومهم سياسياً أو اقتصادياً أو أمنياً أو اجتماعياً؟
    2. إذا لم يكن مهماً أو لا يخص تونس إطلاقاً، أجب بكلمة واحدة فقط: IGNORE
    3. إذا كان مهماً ويخص تونس، اكتب ملخصاً سريعاً ومكثفاً في نقطتين أو ثلاث أسطر كحد أقصى بأسلوب احترافي ومباشر.
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        
        # استخراج النص من الاستجابة
        if "candidates" in data:
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if "IGNORE" in text.upper():
                return None
            return text
        else:
            print(f"API Error: {data}")
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
        requests.post(url, json=payload)
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
        feed = feedparser.parse(feed_url)
        
        for entry in feed.entries[:5]:  
            entry_id = entry.get("id", entry.get("link"))
            
            if not is_already_sent(entry_id):
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")

                print(f"تحليل الخبر: {title}")
                ai_summary = evaluate_and_summarize(title, summary)
                
                if ai_summary:
                    send_telegram(ai_summary, link)
                    print("-> تم إرسال الخبر للتيليجرام!")
                else:
                    print("-> تم التجاهل (غير مهم أو لا يخص تونس).")
                
                mark_as_sent(entry_id)
                
    print("\nانتهت العملية بنجاح.")

if __name__ == "__main__":
    main()
