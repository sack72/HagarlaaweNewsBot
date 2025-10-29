import os
import time
import re
import asyncio
import logging
import feedparser
from telegram import Bot
from openai import AsyncOpenAI
import httpx
import sys
from typing import Optional, List, Dict, Any

###############################################################################
# 1. Environment & Setup
###############################################################################
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
RSS_URLS_RAW        = os.getenv("RTT_RSS_FEED_URL", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, RSS_URLS_RAW, OPENAI_API_KEY]):
    logging.error("Missing required environment variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

###############################################################################
# 2. Persistent Storage
###############################################################################
PERSISTENT_STORAGE_PATH = "/bot-data"
LAST_LINK_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")
LAST_PUBLISHED_TIME_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_published_time.txt")

def load_last_posted_link() -> Optional[str]:
    if os.path.isfile(LAST_LINK_FILE):
        try:
            with open(LAST_LINK_FILE, "r") as f:
                return f.readline().strip() or None
        except IOError:
            return None
    return None

def save_last_posted_link(link: str) -> None:
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_LINK_FILE, "w") as f:
        f.write(link)

def load_last_published_time() -> float:
    if os.path.isfile(LAST_PUBLISHED_TIME_FILE):
        try:
            with open(LAST_PUBLISHED_TIME_FILE, "r") as f:
                return float(f.read().strip())
        except:
            return 0.0
    return 0.0

def save_last_published_time(timestamp: float) -> None:
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_PUBLISHED_TIME_FILE, "w") as f:
        f.write(str(timestamp))

###############################################################################
# 3. Financial Glossary
###############################################################################
GLOSSARY = {
    "futures": "qandaraasyada mustaqbalka",
    "yields": "wax-soo-saarka bonds-ka",
    "bond": "bond",
    "rate cut": "hoos u dhigidda heerka dulsaarka",
    "rate hike": "kor u qaadista heerka dulsaarka",
    "inflation": "sicirka maciishadda",
    "CPI": "CPI",
    "core inflation": "sicir-bararka asaasiga ah",
    "central bank": "bangiga dhexe",
    "Federal Reserve": "Bangiga Dhexe ee Maraykanka",
    "RBA": "Bangiga Dhexe ee Australiya",
    "BOE": "Bangiga Ingiriiska",
    "BOJ": "Bangiga Japan",
    "ECB": "Bangiga Yurub",
    "GDP": "wax-soo-saarka guud ee dalka",
    "recession": "hoos u dhac dhaqaale",
    "unemployment": "shaqo la'aan",
    "employment": "shaqaalaysiinta"
}

def apply_glossary(text: str) -> str:
    for eng, som in GLOSSARY.items():
        pattern = re.compile(r"\b" + re.escape(eng) + r"\b", re.IGNORECASE)
        text = pattern.sub(som, text)
    return text

###############################################################################
# 4. Somali Translation
###############################################################################
async def translate_to_somali(text: str) -> str:
    try:
        logging.info(f"📝 Translating: {text}")
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

            step1 = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "Translate this financial news into Somali clearly and accurately."},
                    {"role": "user", "content": text}
                ],
                temperature=0.2,
                max_tokens=300,
            )
            first_pass = step1.choices[0].message.content.strip()

            step2 = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "Rewrite in professional Somali financial-news style, concise and clear."},
                    {"role": "user", "content": first_pass}
                ],
                temperature=0.3,
                max_tokens=300,
            )
            result = apply_glossary(step2.choices[0].message.content.strip())
            logging.info(f"✅ Somali ready: {result[:70]}...")
            return result
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        return ""

###############################################################################
# 5. Facebook Posting
###############################################################################
async def post_to_facebook(message: str) -> None:
    """Posts translated Somali message to Facebook Page."""
    page_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("FACEBOOK_PAGE_ID")

    if not page_token or not page_id:
        logging.warning("⚠️ Facebook credentials not set. Skipping Facebook post.")
        return

    fb_url = f"https://graph.facebook.com/{page_id}/feed"
    data = {"message": message, "access_token": page_token}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(fb_url, data=data)
            if response.status_code == 200:
                logging.info("✅ Posted to Facebook successfully.")
            else:
                logging.error(f"❌ Facebook post failed: {response.text}")
    except Exception as e:
        logging.error(f"❌ Facebook error: {e}")

###############################################################################
# 6. Filters & Cleaning
###############################################################################
TARGET_FOREX_NEWS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "JPY": "🇯🇵", "GBP": "🇬🇧",
    "CAD": "🇨🇦", "CHF": "🇨🇭", "AUD": "🇦🇺", "NZD": "🇳🇿",
    "United States": "🇺🇸", "Europe": "🇪🇺", "Japan": "🇯🇵", "UK": "🇬🇧",
    "Canada": "🇨🇦", "Swiss": "🇨🇭", "Australia": "🇦🇺", "New Zealand": "🇳🇿"
}

EXCLUSION_KEYWORDS = ["auction", "bid-to-cover", "Energy", "Coal", "NATO"]

def should_exclude_headline(title: str) -> bool:
    title_lower = title.lower()
    for k in EXCLUSION_KEYWORDS:
        if k.lower() in title_lower:
            logging.info(f"🚫 Excluded: {title}")
            return True
    return False

def clean_title(t: str) -> str:
    t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}:?\s*", "", t)
    return re.sub(r"^[^:]+:\s*", "", t).strip()

###############################################################################
# 7. Fetch & Post Headlines
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
    last_link = load_last_posted_link()
    last_time = load_last_published_time()
    new_items = []

    for url in RSS_URLS:
        logging.info(f"🔄 Fetching feed: {url}")
        feed = feedparser.parse(url)
        logging.info(f"✅ Found {len(feed.entries)} entries")

        for e in feed.entries:
            link = e.get("link")
            pub = e.get("published_parsed")

            if link == last_link:
                logging.info("⛔ Reached last posted link. Stopping scan for this feed.")
                break

            if pub and time.mktime(pub) <= last_time:
                continue

            new_items.append(e)

    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())

    if not new_items:
        logging.info("📭 No new items")
        return

    latest_timestamp = last_time

    for e in new_items:
        raw = e.title or ""
        logging.info(f"📰 Found headline: {raw}")

        if should_exclude_headline(raw):
            continue

        flag = None
        for c, f in TARGET_FOREX_NEWS.items():
            if re.search(r"\b" + re.escape(c) + r"\b", raw, re.IGNORECASE):
                flag = f
                break

        # 🔍 Detect Political / Central Bank Market Movers
        IMPORTANT_KEYWORDS = [
            "Trump", "Biden", "White House", "Election", "Republican", "Democrat",
            "Powell", "Fed", "Federal Reserve", "FOMC",
            "Yellen", "Treasury Secretary",
            "ECB", "Lagarde", "Bank of Japan", "BOJ",
            "RBA", "Philip Lowe", "RBNZ", "BOE", "Andrew Bailey",
            "SNB", "Jordan", "Bank of Canada", "BoC", "Tiff Macklem",
            "China PBOC", "PBoC", "Xi Jinping", "Beijing policy"
        ]

        if not flag:
            if any(re.search(r"\b" + re.escape(k) + r"\b", raw, re.IGNORECASE) for k in IMPORTANT_KEYWORDS):
                logging.info(f"🏛️ Important macro headline detected: {raw}")
                flag = "🇺🇸"
            else:
                logging.info(f"❎ No target currency or macro keyword found in: {raw}")
                continue

        title = clean_title(raw)
        somali = await translate_to_somali(title)
        if not somali:
            continue

        message = f"{flag} {somali}"
        logging.info(f"📤 Posting to Telegram: {message[:80]}...")

        try:
            # Telegram Post
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )

            # Facebook Post
            await post_to_facebook(message)

            if e.get("link"):
                save_last_posted_link(e.get("link"))
            if e.get("published_parsed"):
                latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))

        except Exception as err:
            logging.error(f"❌ Telegram or Facebook post failed: {err}")

        await asyncio.sleep(1)

    if latest_timestamp > last_time:
        save_last_published_time(latest_timestamp)

###############################################################################
# 8. Main Runner
###############################################################################
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    while True:
        logging.info("♻️ Checking for new headlines...")
        try:
            await fetch_and_post_headlines(bot)
        except Exception as e:
            logging.exception("❌ Fatal error. Restarting in 60s.")
        logging.info("⏳ Sleeping 60 seconds...\n")
        await asyncio.sleep(60)

if __name__ == "__main__":
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    asyncio.run(main())
