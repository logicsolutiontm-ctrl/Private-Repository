import os
import json
import feedparser
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FEEDS = [
    "https://www.aljazeera.net/feed",
    "https://www.mosaiquefm.net/ar/rss",
    "https://tunisie-telegraph.com/feed/",
    "https://feeds.bbci.co.uk/arabic/rss.xml",
    "https://www.france24.com/ar/rss",
]

SEEN_FILE = "seen.json"
MAX_PER_RUN = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}


def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen)[-2000:], f, ensure_ascii=False)


def summarize(title, text):
    if not GEMINI_API_KEY:
        return ""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    prompt = f"لخّص هذا الخبر في جملتين بالعربية:\n{title}\n{text[:1500]}"
    try:
        r = requests.post(
            url,
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print("Gemini error:", e)
        return ""


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message[:4000]},
        timeout=30,
    )
    if not r.ok:
        print("Telegram error:", r.status_code, r.text)
    return r.ok


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    seen = load_seen()
    first_run = len(seen) == 0
    new_items = []

    for feed_url in FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"Feed failed {feed_url}: {e}")
            continue

        print(f"{feed_url}: {len(feed.entries)} entries")
        for entry in feed.entries[:10]:
            uid = entry.get("id") or entry.get("link")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            new_items.append(entry)

    # On the very first run, only send a few so you don't get flooded
    limit = 3 if first_run else MAX_PER_RUN
    for entry in new_items[:limit]:
        title = entry.get("title", "")
        summary = summarize(title, entry.get("summary", ""))
        msg = f"📰 {title}\n\n{summary}\n\n{entry.get('link', '')}".strip()
        send_telegram(msg)

    save_seen(seen)
    print(f"Done. New items: {len(new_items)}")


if __name__ == "__main__":
    main()
