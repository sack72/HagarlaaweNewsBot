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

# Check for environment variables
if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, RSS_URLS_RAW, OPENAI_API_KEY]):
    logging.error("Missing required environment variables.")
    sys.exit(1) 

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

###############################################################################
# 2. Persistent Storage (Requires Render Disk/Volume)
###############################################################################
PERSISTENT_STORAGE_PATH = "/bot-data"
LAST_LINK_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")
LAST_PUBLISHED_TIME_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_published_time.txt")


def load_last_posted_link() -> Optional[str]:
    """Loads the link of the last successfully posted entry."""
    if os.path.isfile(LAST_LINK_FILE):
        try:
            with open(LAST_LINK_FILE, 'r') as f:
                return f.readline().strip() or None
        except IOError as e:
            logging.error(f"Error loading last link: {e}")
            return None
    return None

def save_last_posted_link(link: str) -> None:
    """Saves the link of the newly posted entry."""
    try:
        os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
        with open(LAST_LINK_FILE, "w") as f:
            f.write(link)
    except IOError as e:
        logging.error(f"Error saving last link: {e}")

def load_last_published_time() -> float:
    """Loads the timestamp of the last *successful* post (prevents reposting old news)."""
    if os.path.isfile(LAST_PUBLISHED_TIME_FILE):
        try:
            with open(LAST_PUBLISHED_TIME_FILE, "r") as f:
                return float(f.read().strip())
        except (IOError, ValueError) as e:
            logging.warning(f"Error loading or parsing last published time. Resetting to 0.0. Error: {e}")
            return 0.0
    return 0.0

def save_last_published_time(timestamp: float) -> None:
    """Saves the timestamp of the last successfully posted news."""
    try:
        os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
        with open(LAST_PUBLISHED_TIME_FILE, "w") as f:
            f.write(str(timestamp))
    except IOError as e:
        logging.error(f"Error saving last published time: {e}")

###############################################################################
# 3. Translation
###############################################################################
async def translate_to_somali(text: str) -> str:
    """Translates the given English text to Somali using OpenAI."""
    try:
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
    except Exception as e:
        logging.error(f"OpenAI Translation failed: {e}")
        return f"[Translation Error: Unable to translate. Original: {text}]"

###############################################################################
# 4. Core Logic & Filtering
###############################################################################

# Define a dictionary of target countries and their corresponding flags
TARGET_FOREX_NEWS = {
    'US': '🇺🇸', 'USA': '🇺🇸', 'United States': '🇺🇸', 'USD': '🇺🇸',
    'Euro': '🇪🇺', 'EUR': '🇪🇺', 'Europe': '🇪🇺', 'EU': '🇪🇺',
    'Japan': '🇯🇵', 'JPY': '🇯🇵',
    'UK': '🇬🇧', 'Britain': '🇬🇧', 'Great Britain': '🇬🇧', 'GBP': '🇬🇧',
    'Canada': '🇨🇦', 'CAD': '🇨🇦',
    'Switzerland': '🇨🇭', 'Swiss': '🇨🇭', 'CHF': '🇨🇭',
    'Australia': '🇦🇺', 'AUD': '🇦🇺',
    'New Zealand': '🇳🇿', 'NZD': '🇳🇿',
}

# Keywords to exclude (case-insensitive)
EXCLUSION_KEYWORDS = [
    # Geopolitical/Military
    "NATO", "Whittaker", 
    # Treasury/Govt Bills
    "treasury to auction", "treasury bills", "sell $", "bill high yield", 
    "bill bid-to-cover", "bill auction", 
    # Specific Analyst/Bank Names/Reports
    "credit agricole", "mufg", "berenberg", 
    # Commodities/Specific Industry
    "lng spot contract price", "baytex energy",
    # Regulation/Housing
    "fsa to tighten insider scrutiny", "freddie mac"
]


def should_exclude_headline(title: str) -> bool:
    """Checks if the headline contains any of the exclusion keywords."""
    title_lower = title.lower()
    for keyword in EXCLUSION_KEYWORDS:
        # Use word boundaries for cleaner matches unless the keyword is a compound name
        if re.search(r'\b' + re.escape(keyword.lower()) + r'\b', title_lower):
            return True
        # Check for compound names (like 'credit agricole' when the source might not use spaces or punctuation)
        if keyword.lower() in title_lower:
             return True
    return False

def clean_title(title_raw: str) -> str:
    """Removes flags and feed prefixes from the title."""
    # 1) Remove any flag emojis (unicode range) to avoid duplicates
    title = re.sub(r'[\U0001F1E6-\U0001F1FF]{2}:?\s*', '', title_raw, flags=re.UNICODE).strip()
    # 2) Remove feed prefix like "FinancialJuice:"
    title = re.sub(r'^[^:]+:\s*', '', title).strip()
    return title

async def fetch_and_post_headlines(bot: Bot):
    last_link = load_last_posted_link()
    last_published_time = load_last_published_time()
    all_new_entries: List[Dict[str, Any]] = []

    for url in RSS_URLS:
        logging.info("Fetching %s", url)
        # FIX: Removed 'timeout=10' from feedparser.parse() to fix TypeError
        feed = feedparser.parse(url) 
        
        new_entries = []
        for entry in feed.entries:
            link = entry.get("link")
            published_time = entry.get("published_parsed")

            # 1. Check against last posted link (to avoid duplicates from prior runs)
            if link and link == last_link:
                break
            
            # 2. Check against last posted time (to avoid re-posting old news on restart/failure)
            if published_time and time.mktime(published_time) <= last_published_time:
                 # We continue here instead of break because RSS feeds aren't always strictly chronological
                 continue 

            new_entries.append(entry)
        new_entries.reverse()
        all_new_entries.extend(new_entries)

    # Sort all new entries chronologically to post in the correct order
    all_new_entries.sort(key=lambda e: e.get("published_parsed") or time.gmtime())

    if not all_new_entries:
        logging.info("No new headlines.")
        return

    # Track the latest timestamp for saving after successful posts
    latest_timestamp = last_published_time

    for entry in all_new_entries:
        title_raw = entry.title if hasattr(entry, "title") else ""
        link = entry.link if hasattr(entry, "link") else None
        
        # 3. Exclude unwanted news first
        if not title_raw or should_exclude_headline(title_raw):
            logging.info(f"Skipping excluded/empty news: {title_raw}")
            continue

        # 4. Check if it's a target Forex country (Country filter)
        found_country_flag = None
        for country_name, flag in TARGET_FOREX_NEWS.items():
            if re.search(r'\b' + re.escape(country_name) + r'\b', title_raw, re.IGNORECASE):
                found_country_flag = flag
                break

        if not found_country_flag:
            logging.info(f"Skipping non-Forex news: {title_raw}")
            continue

        title = clean_title(title_raw)
        
        # 5. Translate and build the message
        somali_text = await translate_to_somali(title)
        message_to_send = f"{found_country_flag} **{title}**\n\n🇸🇴 {somali_text}"

        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            
            # Update latest_timestamp and link only on successful post
            published_time = entry.get("published_parsed")
            if published_time:
                latest_timestamp = max(latest_timestamp, time.mktime(published_time))
                
            if link:
                save_last_posted_link(link)
                
        except Exception as e:
            logging.error(f"Telegram send failed for: {title}. Error: {e}")

        # Be polite and wait a bit between posts
        await asyncio.sleep(1)

    # Save the latest successful post time *after* the loop completes
    if latest_timestamp > load_last_published_time():
        save_last_published_time(latest_timestamp)
        logging.info(f"Updated last successfully posted time to {latest_timestamp}")

###############################################################################
# 5. Main runner
###############################################################################
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    while True:
        try:
            await fetch_and_post_headlines(bot)
        except Exception as e:
            logging.exception("Main-loop encountered a fatal error. Restarting in 60s.")
        # Wait period for the next cycle
        await asyncio.sleep(60)

if __name__ == "__main__":
    # Ensure the storage path exists on startup
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    asyncio.run(main())
