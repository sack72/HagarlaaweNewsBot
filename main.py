import os
import time
import re
import asyncio
import logging

import feedparser
import pytz
from telegram import Bot
from openai import AsyncOpenAI
import httpx

# ------------------------------------------------------------------
# 1. Config
# ------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is missing")

PERSISTENT_STORAGE_PATH = "/bot-data"
LAST_LINK_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")

# ------------------------------------------------------------------
# 2. Global helpers
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)

# Updated KEYWORDS list to filter for specific trades, politicians, and economic reports
KEYWORDS = [
    "AUD", "GBP", "EUR", "NZD", "USD", "JPY", "CAD", "CHF", "forex",
    "trade", "market", "gold", "silver", "oil", "crude", "crypto", "bitcoin",
    "ethereum", "Fed", "ECB", "BOJ", "inflation", "rate", "Powell",
    "Lagarde", "Trump", "Biden", "Putin", "Xi", "sunak", "macron",
    "cpi", "ppi", "jobs", "employment", "non-farm payrolls", "nfp", "unemployment",
    "interest rate", "retail sales", "gdp"
]

SOMALI_PREFIXES_TO_REMOVE = [
    "Qeybta Abaalmarinta:", "Qeyb-qabad:", "Qeyb-dhaqameedka", "Qeyb-dhaqaale:",
    "Fieldinice:", "Fieldjuice:", "Dhaqaale:", "Abuurjuice:",
]

def contains_keywords(text, keywords):
    if not keywords:
        return True
    return any(k.lower() in text.lower() for k in keywords)

def remove_flag_emojis(text):
    return re.sub(r'[\U0001F1E6-\U0001F1FF]{2}:?\s*', '', text, flags=re.UNICODE).strip()

def load_last_posted_link():
    try:
        if os.path.isfile(LAST_LINK_FILE):
            with open(LAST_LINK_FILE) as f:
                return f.readline().strip() or None
    except Exception as e:
        logging.warning("Could not read %s: %s", LAST_LINK_FILE, e)
    return None

def save_last_posted_link(link):
    try:
        os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
        with open(LAST_LINK_FILE, "w") as f:
            f.write(link)
    except Exception as e:
        logging.error("Could not save last link: %s", e)

# ------------------------------------------------------------------
# 3. Translation (async)
# ------------------------------------------------------------------
async def translate_text_with_gpt(text, lang="Somali"):
    try:
        async with httpx.AsyncClient() as http_client:
            openai_client = AsyncOpenAI(
                api_key=OPENAI_API_KEY,
                http_client=http_client
            )
            response = await openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a professional translator. Translate the financial news text into {lang}. Preserve tone and meaning."
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            translated = response.choices[0].message.content.strip()
            for prefix in SOMALI_PREFIXES_TO_REMOVE:
                if translated.startswith(prefix):
                    translated = translated[len(prefix):].strip()
            return translated
    except Exception as e:
        logging.exception("Translation failed: %s", e)
        return f"Translation unavailable. Original: {text}"

# ------------------------------------------------------------------
# 4. Fetch & post
# ------------------------------------------------------------------
async def fetch_and_post_headlines(bot):
    last_link = load_last_posted_link()
    logging.info("Fetching %s", FINANCIAL_JUICE_RSS_FEED_URL)
    feed = feedparser.parse(FINANCIAL_JUICE_RSS_FEED_URL)

    new_entries = []
    for entry in feed.entries:
        if hasattr(entry, "link") and entry.link == last_link:
            break
        new_entries.append(entry)
    new_entries.reverse()

    if not new_entries:
        logging.info("No new headlines.")
        return

    for entry in new_entries:
        title = entry.title
        link = entry.link if hasattr(entry, "link") else None

        title = remove_flag_emojis(title.replace("FinancialJuice:", "").replace("Abuurjuice:", "").strip())
        if not contains_keywords(title, KEYWORDS):
            continue

        logging.info("Translating: %s", title)
        somali = await translate_text_with_gpt(title)

        # Updated message format to only include the translated post
        message = f"**DEGDEG 🔴**\n\n{somali}"
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            if link:
                save_last_posted_link(link)
        except Exception as e:
            logging.error("Telegram send failed: %s", e)

        await asyncio.sleep(1)

# ------------------------------------------------------------------
# 5. Main loop
# ------------------------------------------------------------------
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    while True:
        try:
            await fetch_and_post_headlines(bot)
        except Exception as e:
            logging.exception("Main loop error: %s", e)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
