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
import discord  # discord.py >= 2.3.2

###############################################################################
# 1. Environment variables
###############################################################################
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
DISCORD_BOT_TOKEN   = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID  = int(os.getenv("DISCORD_CHANNEL_ID") or 0)
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, DISCORD_BOT_TOKEN,
            DISCORD_CHANNEL_ID, FINANCIAL_JUICE_RSS_FEED_URL, OPENAI_API_KEY]):
    raise ValueError("One or more required environment variables are missing.")

###############################################################################
# 2. Logging
###############################################################################
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

###############################################################################
# 3. Keyword filter
###############################################################################
KEYWORDS = [
    # FX pairs
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
    "EURGBP", "GBPJPY", "EURJPY", "AUDJPY", "AUDNZD", "GBPAUD", "GBPCAD",
    # Metals & oil
    "XAUUSD", "GOLD", "XAGUSD", "SILVER", "OIL", "WTI", "BRENT",
    # Politicians & central banks
    "TRUMP", "BIDEN", "POWELL", "YELLEN", "BASSETT", "KLINGBEIL", "SCHOLZ",
    "MACRON", "SUNAK", "FED", "ECB", "BOE", "BOJ", "RBA", "SNB"
]

def contains_keywords(text: str) -> bool:
    return any(k in text.upper() for k in KEYWORDS)

###############################################################################
# 4. Remove flags / clean title
###############################################################################
def remove_flag_emojis(text: str) -> str:
    return re.sub(r'[\U0001F1E6-\U0001F1FF]{2}:?\s*', '', text, flags=re.UNICODE).strip()

###############################################################################
# 5. Persistent storage
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
# 6. Translation
###############################################################################
SOMALI_PREFIXES_TO_REMOVE = [
    "Qeybta Abaalmarinta:", "Qeyb-qabad:", "Qeyb-dhaqameedka", "Qeyb-dhaqaale:",
    "Fieldinice:", "Fieldjuice:", "Dhaqaale:", "Abuurjuice:",
]

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
            max_tokens=1000
        )
        translated = response.choices[0].message.content.strip()
        for prefix in SOMALI_PREFIXES_TO_REMOVE:
            if translated.startswith(prefix):
                translated = translated[len(prefix):].strip()
        return translated

###############################################################################
# 7. Discord client
###############################################################################
discord_client = discord.Client(intents=discord.Intents.default())

async def post_to_discord(text: str) -> None:
    await discord_client.wait_until_ready()
    channel = discord_client.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        try:
            await channel.send(text)
        except Exception as e:
            logging.error("Discord send failed: %s", e)

###############################################################################
# 8. Core fetch-post loop
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
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
        title_raw = entry.title
        link = entry.link if hasattr(entry, "link") else None

        title = remove_flag_emojis(
            title_raw.replace("FinancialJuice:", "").replace("Abuurjuice:", "").strip()
        )
        if not contains_keywords(title):
            continue

        logging.info("Processing: %s", title)
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

        # Discord
        await post_to_discord(somali_text)

        if link:
            save_last_posted_link(link)

        await asyncio.sleep(1)

###############################################################################
# 9. Main runner
###############################################################################
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    # Start Discord client in background
    asyncio.create_task(discord_client.start(DISCORD_BOT_TOKEN))

    while True:
        try:
            await fetch_and_post_headlines(bot)
        except Exception as e:
            logging.exception("Main-loop error: %s", e)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
