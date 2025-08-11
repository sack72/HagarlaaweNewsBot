import os
import asyncio
import logging
import feedparser
from telegram import Bot
from openai import AsyncOpenAI
import httpx

# ---------- ENV ----------
BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID         = os.getenv("TELEGRAM_CHANNEL_ID")
FEED_URLS       = [u.strip() for u in os.getenv("RSS_FEED_URLS", "").split(",") if u.strip()]
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")

if not all([BOT_TOKEN, CHAT_ID, FEED_URLS, OPENAI_API_KEY]):
    raise ValueError("Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, RSS_FEED_URLS, and OPENAI_API_KEY")

# ---------- PERSISTENCE ----------
LAST_FILE = "/bot-data/last_link.txt"

def load_last():
    if os.path.isfile(LAST_FILE):
        with open(LAST_FILE) as f:
            return f.readline().strip()
    return None

def save_last(link):
    os.makedirs(os.path.dirname(LAST_FILE), exist_ok=True)
    with open(LAST_FILE, "w") as f:
        f.write(link)

# ---------- TRANSLATION ----------
async def to_somali(text: str) -> str:
    async with httpx.AsyncClient() as http_client:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
        resp = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Translate the following English text into Somali. Keep the tone and meaning."},
                {"role": "user", "content": text}
            ],
            temperature=0.3,
            max_tokens=1000
        )
        return resp.choices[0].message.content.strip()

# ---------- POSTING ----------
async def post_new_entries(bot: Bot):
    last = load_last()
    new_entries = []

    for url in FEED_URLS:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            if entry.link == last:
                break
            new_entries.append(entry)
        new_entries.reverse()

    if not new_entries:
        logging.info("Nothing new.")
        return

    for entry in new_entries:
        english = entry.title
        somali  = await to_somali(english)
        text    = f"{english}\n\n🇸🇴 {somali}\n\n{entry.link}"
        await bot.send_message(chat_id=CHAT_ID, text=text, disable_web_page_preview=True)
        save_last(entry.link)
        await asyncio.sleep(1)

# ---------- MAIN ----------
async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
    bot = Bot(token=BOT_TOKEN)
    while True:
        try:
            await post_new_entries(bot)
        except Exception as e:
            logging.exception("Error: %s", e)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
