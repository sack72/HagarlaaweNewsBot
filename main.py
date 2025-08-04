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
# 1. Configuration (from environment)
# ------------------------------------------------------------------
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID  = os.getenv("TELEGRAM_CHANNEL_ID")
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL")
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is missing!")

# ------------------------------------------------------------------
# 2. Filter keywords (Forex, metals, oil, politicians, central banks)
# ------------------------------------------------------------------
KEYWORDS = [
    # FX majors & minors
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
    "EURGBP", "GBPJPY", "EURJPY", "AUDJPY", "AUDNZD", "GBPAUD", "GBPCAD",
    # Metals & oil
    "XAUUSD", "XAGUSD", "GOLD", "SILVER", "OIL", "WTI", "BRENT",
    # Politicians / central bankers
    "TRUMP", "BIDEN", "POWELL", "YELLEN", "BASSETT", "KLINGBEIL",
    "SCHOLZ", "MACRON", "SUNAK", "BOJO", "FED", "ECB", "BOE", "BOJ", "RBA", "SNB",
]

# Somali prefixes we strip after translation
SOMALI_PREFIXES_TO_REMOVE = [
    "Qeybta Abaalmarinta:", "Qeyb-qabad:", "Qeyb-dhaqameedka", "Qeyb-dhaqaale:",
    "Fieldinice:", "Fieldjuice:", "Dhaqaale:", "Abuurjuice:",
]

# ------------------------------------------------------------------
# 3. Persistent storage
# ------------------------------------------------------------------
PERSISTENT_STORAGE_PATH = "/bot-data"
LAST_LINK_FILE          = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")

logging.basicConfig(level=logging.INFO)

# ------------------------------------------------------------------
# 4. Small helpers
# ------------------------------------------------------------------
def contains_keywords(text: str, keywords) -> bool:
    text_upper = text.upper()
    return any(k in text_upper for k in keywords)

def remove_flag_emojis(text: str) -> str:
    return re.sub(r'[\U0001F1E6-\U0001F1FF]{2}:?\s*', '', text, flags=re.UNICODE).strip()

def load_last_posted_link():
    if os.path.isfile(LAST_LINK_FILE):
        with open(LAST_LINK_FILE) as f:
            return f.readline().strip() or None
    return None

def save_last_posted_link(link: str):
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_LINK_FILE, "w") as f:
        f.write(link)

# ------------------------------------------------------------------
# 5. Translation (async)
# ------------------------------------------------------------------
async def translate_text_with_gpt(text: str, lang: str = "Somali") -> str:
    async with httpx.AsyncClient() as http_client:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": f"You are a professional translator. Translate the following English financial news into {lang}. Preserve tone and meaning."
                },
                {"role": "user", "content": text}
            ],
            temperature=0.3,
            max_tokens=1000,
        )
        translated = response.choices[0].message.content.strip()

        # Remove unwanted Somali prefixes
        for prefix in SOMALI_PREFIXES_TO_REMOVE:
            if translated.startswith(prefix):
                translated = translated[len(prefix):].strip()
        return translated

# ------------------------------------------------------------------
# 6. Fetch headlines & post loop
# ------------------------------------------------------------------
async def fetch_and_post_headlines(bot: Bot):
    last_link = load_last_posted_link()

    logging.info("Fetching feed: %s", FINANCIAL_JUICE_RSS_FEED_URL)
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
        title_raw = entry.title
        link = entry.link if hasattr(entry, "link") else None

        # Clean title
        title = remove_flag_emojis(
            title_raw.replace("FinancialJuice:", "").replace("Abuurjuice:", "").strip()
        )

        if not contains_keywords(title, KEYWORDS):
            logging.debug("Skipping (no keywords): %s", title)
            continue

        logging.info("Translating: %s", title)
        somali_text = await translate_text_with_gpt(title)

        # Post only the Somali translation
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=somali_text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            if link:
                save_last_posted_link(link)
        except Exception as e:
            logging.error("Telegram send failed: %s", e)

        await asyncio.sleep(1)  # 1-second throttle

# ------------------------------------------------------------------
# 7. Main entry
# ------------------------------------------------------------------
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    while True:
        try:
            await fetch_and_post_headlines(bot)
        except Exception as e:
            logging.exception("Main-loop error: %s", e)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
