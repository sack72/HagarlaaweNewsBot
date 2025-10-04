import os
import time
import asyncio
import logging
import feedparser
import httpx
from telegram import Bot
from openai import AsyncOpenAI

###############################################################################
# 1. Environment Variables
###############################################################################
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
RSS_URLS_RAW        = os.getenv("RTT_RSS_FEED_URL", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, RSS_URLS_RAW, OPENAI_API_KEY]):
    raise ValueError("Missing required environment variables.")

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]
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
# 3. Translation
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
# 4. Core loop: Fetch & Post all RSS items
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
    last_link = load_last_posted_link()
    all_new_entries = []

    for url in RSS_URLS:
        logging.info("Fetching %s", url)
        feed = feedparser.parse(url)

        new_entries = []
        for entry in feed.entries:
            if hasattr(entry, "link") and entry.link == last_link:
                break
            new_entries.append(entry)
        new_entries.reverse()
        all_new_entries.extend(new_entries)

    # Sort by published date (most recent last)
    all_new_entries.sort(key=lambda e: e.get("published_parsed") or time.gmtime())

    if not all_new_entries:
        logging.info("No new headlines.")
        return

    for entry in all_new_entries:
        title_raw = entry.title
        link = entry.link if hasattr(entry, "link") else None

        # Clean title: remove emojis or feed prefixes
        title = title_raw
        title = title.strip()

        # Translate to Somali
        somali_text = await translate_to_somali(title)

        # Build final message
        message_to_send = f"{title}\n\n🇸🇴 {somali_text}"
        if link:
            message_to_send += f"\n🔗 {link}"

        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            logging.info(f"Posted: {title}")
        except Exception as e:
            logging.error("Telegram send failed: %s", e)

        # Save last posted link
        if link:
            save_last_posted_link(link)

        await asyncio.sleep(1)  # small delay between posts

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
        await asyncio.sleep(60)  # check every minute

if __name__ == "__main__":
    asyncio.run(main())            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logging.error("Telegram send failed: %s", e)

        if link:
            save_last_posted_link(link)

        await asyncio.sleep(1)

