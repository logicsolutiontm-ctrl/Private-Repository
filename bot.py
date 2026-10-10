import os
import sqlite3
import feedparser
import requests
import google.generativeai as genai

# إعداد مفاتيح التشغيل عبر متغيرات البيئة
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# قائمة مصادر الأخبار (RSS Feeds)
FEEDS = [
    "https://www.aljazeera.net/feed",
    "https://www.mosaiquefm.net/ar/rss", # مصدر تونسي سريع ومباشر
]

def init_db():
    conn = sqlite3.connect("sent_news.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY
        )
    """)
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

def evaluate_and_summarize(title, summary):
    prompt = f"""
    أنت محرر إخباري ذكي متخصص في الشأن التونسي.
    قيم الخبر التالي:
    العنوان: {title}
    التفاصيل: {summary}

    الشروط:
    1. هل الخبر متعلق بتونس ومهم سياسياً أو اقتصادياً أو أمنياً أو اجتماعياً بشكل بارز؟
    2. إذا لم يكن مهماً أو لا يخص تونس إطلاقاً، أجب بكلمة واحدة فقط: IGNORE
    3. إذا كان مهماً ويخص تونس، اكتب ملخصاً سريعاً ومكثفاً في نقطتين أو ثلاث أسطر كحد أقصى بأسلوب احترافي ومباشر.
    """
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        if "IGNORE" in text:
            return None
        return text
    except Exception as e:
        print(f"Error calling AI: {e}")
        return None

def send_telegram(text, link):
    message = f"{text}\n\n🔗 رابط المصدر: {link}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False
    }
    requests.post(url, json=payload)

def main():
    init_db()
    for feed_url in FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:10]:  # فحص آخر 10 أخبار لكل مصدر
            entry_id = entry.get("id", entry.get("link"))
            if not is_already_sent(entry_id):
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")

                ai_summary = evaluate_and_summarize(title, summary)
                if ai_summary:
                    send_telegram(ai_summary, link)
                
                mark_as_sent(entry_id)

if __name__ == "__main__":
    main()