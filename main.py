import os
import time
import re
import asyncio
import logging

import feedparser
from telegram import Bot
from openai import AsyncOpenAI
import httpx

###############################################################################
# 1. Environment variables
###############################################################################
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
RTT_RSS_FEED_URL    = os.getenv("RTT_RSS_FEED_URL")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, RTT_RSS_FEED_URL, OPENAI_API_KEY]):
    raise ValueError("Missing required environment variables.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

###############################################################################
# 2. Persistent storage
###############################################################################
PERSISTENT_STORAGE_PATH = "/bot-data"
LAST_LINK_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")

def load_last_posted_link() -> str | None:
    if os.path.isfile(LAST_LINK_FILE):
        with open(LAST_LINK_FILE) as f:
            return f.readline().strip() or None
    return None

def save_last_posted_link(link: str) -> None:
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_LINK_FILE, "w") as f:
        f.write(link)

###############################################################################
# 3. Translation (Somali only)
###############################################################################
async def translate_to_somali(text: str) -> str:
    async with httpx.AsyncClient() as http_client:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional translator. Translate the following English financial news into Somali. Preserve tone and meaning."
                },
                {"role": "user", "content": text}
            ],
            temperature=0.3,
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()

###############################################################################
# 4. Core loop
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
    last_link = load_last_posted_link()
    logging.info("Fetching %s", RTT_RSS_FEED_URL)
    feed = feedparser.parse(RTT_RSS_FEED_URL)

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
        title = re.sub(r'[\U0001F1E6-\U0001F1FF]{2}:?\s*', '', title_raw, flags=re.UNICODE).strip()

        logging.info("Translating: %s", title)
        somali_text = await translate_to_somali(title)

        # Telegram
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=somali_text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logging.error("Telegram send failed: %s", e)

        if link:
            save_last_posted_link(link)

        await asyncio.sleep(1)

###############################################################################
# 5. Main runner
###############################################################################
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
