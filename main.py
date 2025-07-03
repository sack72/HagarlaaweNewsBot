import os
import requests
import json
import time
from datetime import datetime
import logging
from telegram import Bot
from telegram.error import TelegramError
import pytz
import feedparser
import calendar
import aiohttp # Added for asynchronous HTTP requests for LibreTranslate

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration (from Environment Variables) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
# LibreTranslate API Configuration
LIBRETRANSLATE_API_URL = os.getenv("LIBRETRANSLATE_API_URL", "https://translate.argosopentech.com/translate")
LIBRETRANSLATE_API_KEY = os.getenv("LIBRETRANSLATE_API_KEY") # Optional: if your instance requires an API key

# --- Constants ---
LAST_PROCESSED_TIMESTAMP_FILE = "last_processed_timestamp.txt"
FINANCIALJUICE_RSS_FEED_URL = "https://www.financialjuice.com/feed.ashx?xy=rss"

# --- Keyword Definitions for Smarter Filtering (Important for Forex Traders) ---
# High-priority keywords: If any of these are found, the news is almost certainly relevant.
HIGH_PRIORITY_KEYWORDS = [
    # Central Banks & Officials
    "fed", "ecb", "boj", "boe", "rba", "rbnz", "snb", "boc", # Common central bank acronyms
    "federal reserve", "european central bank", "bank of japan", "bank of england",
    "reserve bank of australia", "reserve bank of new zealand", "swiss national bank", "bank of canada",
    "powell", "lagarde", "bailey", "ueda", "christine lagarde", "jerome powell", # Key Central Bankers
    "yellen", "treasury secretary", "finance minister", "chancellor of the exchequer", # Key Finance/Treasury Officials
    "central bank", "monetary policy", "policy meeting", "interest rate decision", "rate hike", "rate cut", # Core Policy Terms

    # Economic Data & Events
    "inflation", "gdp", "unemployment", "cpi", "ppi", "pmi", "nfp", "non-farm payrolls", "jobs report", # Key Economic Data
    "retail sales", "industrial production", "trade balance", "consumer confidence", # More Economic Data
    "economic data", "data release", "report", "forecast", # General data terms
    "speech", "testimony", "press conference", "briefing", # Communication types
    "summit", "g7", "g20", "davos", "imf", "world bank", # Major Global Meetings/Institutions

    # Geopolitical & Market Movers
    "trump", "biden", "election", "geopolitical", "trade war", "tariff", "sanctions", "brexit", # Political/Geopolitical
    "recession", "crisis", "default", "stimulus", "quantitative easing", "qe", "quantitative tightening", "qt", # Major Economic Shifts
    "market", "forex", "fx", "currency", "bond", "stock", "equity", "commodity", "oil", "gold", # Market terms
    "headline" # To capture anything explicitly tagged as 'headline' in content, though usually redundant.
]

# General interest keywords: Broader terms. News needs at least one of these (and not necessarily a high-priority one).
GENERAL_INTEREST_KEYWORDS = [
    "usd", "eur", "jpy", "gbp", "cad", "aud", "nzd", "chf", # Major Currencies
    "btc", "eth", "bitcoin", "ethereum", "crypto", "cryptocurrency", # Crypto
    "xau", "silver", # Precious Metals
    "trading", "investing", "analyst", # Trading/Investment terms
    "company news", "corporate earnings", "dividend" # Company specific financial news
]

EAST_AFRICA_TIMEZONE = pytz.timezone('Africa/Nairobi')

# --- Functions ---

def get_last_processed_timestamp():
    """Retrieves the last processed news timestamp (Unix epoch) from a file or environment variable."""
    if os.path.exists(LAST_PROCESSED_TIMESTAMP_FILE):
        with open(LAST_PROCESSED_TIMESTAMP_FILE, "r") as f:
            try:
                return int(f.read().strip())
            except ValueError:
                return 0 # Default to 0 if file is corrupted or empty
    elif os.getenv("LAST_PROCESSED_TIMESTAMP"):
        try:
            return int(os.getenv("LAST_PROCESSED_TIMESTAMP"))
        except ValueError:
                return 0 # Default to 0 if env var is not a valid number
    return 0 # Default to 0 (epoch start) if no previous record

def save_last_processed_timestamp(timestamp):
    """Saves the last processed news timestamp (Unix epoch) to a file."""
    with open(LAST_PROCESSED_TIMESTAMP_FILE, "w") as f:
        f.write(str(timestamp))

def fetch_latest_news_from_rss():
    """Fetches the latest news from the FinancialJuice RSS feed."""
    logging.info(f"Fetching latest news from RSS feed: {FINANCIALJUICE_RSS_FEED_URL}...")
    try:
        feed = feedparser.parse(FINANCIALJUICE_RSS_FEED_URL)
        if feed.bozo: # Check for parse errors (bozo = True indicates issues)
            logging.error(f"Error parsing RSS feed: {feed.bozo_exception}")
            return None
        
        return feed.entries # Returns a list of news entries
    except Exception as e:
        logging.error(f"Error fetching or parsing RSS feed: {e}")
        return None

async def send_telegram_message(message):
    """Sends a message to the Telegram channel."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram Bot Token or Channel ID not set. Cannot send message.")
        return

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message, parse_mode='HTML', disable_web_page_preview=True)
        logging.info("Message sent to Telegram.")
    except TelegramError as e:
        logging.error(f"Error sending message to Telegram: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while sending Telegram message: {e}")

# --- Translation Function using LibreTranslate ---
async def translate_text_libretranslate(text_to_translate, target_language="som"): # 'som' is the language code for Somali
    """
    Translates text using the LibreTranslate API.
    Returns the translated text or original text on failure.
    """
    if not LIBRETRANSLATE_API_URL:
        logging.warning("LibreTranslate API URL not set. Skipping translation.")
        return text_to_translate # Return original if URL is missing

    try:
        headers = {'Content-Type': 'application/json'}
        payload = {
            "q": text_to_translate,
            "source": "en", # Source language is English
            "target": target_language, # Target language is Somali ('som')
            "format": "text"
        }
        if LIBRETRANSLATE_API_KEY:
            payload["api_key"] = LIBRETRANSLATE_API_KEY

        async with aiohttp.ClientSession() as session:
            async with session.post(LIBRETRANSLATE_API_URL, headers=headers, json=payload) as response:
                response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                data = await response.json()

                if 'translatedText' in data:
                    translated_text = data['translatedText'].strip()
                    logging.info(f"Translated '{text_to_translate}' to '{translated_text}' using LibreTranslate.")
                    return translated_text
                else:
                    logging.error(f"LibreTranslate response missing 'translatedText': {data}")
                    return text_to_translate
    except aiohttp.ClientError as e:
        logging.error(f"LibreTranslate API request error: {e}")
        return text_to_translate
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding LibreTranslate response JSON: {e}")
        return text_to_translate
    except Exception as e:
        logging.error(f"An unexpected error occurred during LibreTranslate translation: {e}")
        return text_to_translate
# --- END LibreTranslate Translation ---

async def main_loop():
    """Main loop to fetch and send news."""
    logging.info("Bot started. Entering main loop...")
    
    # Send a startup message to Telegram for debugging purposes
    try:
        bot_telegram_startup = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot_telegram_startup.send_message(chat_id=TELEGRAM_CHANNEL_ID, text="Bot has started and is checking for new financial news from FinancialJuice RSS. Posts will be filtered by topics and timestamps are in EAT. Now with Somali translation.", parse_mode='HTML') # Updated startup message
        logging.info("Startup message sent to Telegram.")
    except TelegramError as e:
        logging.warning(f"Could not send startup message to Telegram (this might be fine if channel is not ready or ID is not fully correct): {e}")

    while True:
        last_timestamp = get_last_processed_timestamp()
        
        news_entries = fetch_latest_news_from_rss()

        if news_entries and isinstance(news_entries, list):
            fresh_articles = []
            for entry in news_entries:
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    article_timestamp = calendar.timegm(entry.published_parsed)
                else:
                    article_timestamp = int(time.time())
                    logging.warning(f"RSS entry '{entry.title if hasattr(entry, 'title') else 'No title'}' missing published date. Using current time.")

                if article_timestamp > last_timestamp:
                    entry.unix_timestamp = article_timestamp
                    fresh_articles.append(entry)
            
            # 2. Smarter Filtering based on High-Priority and General Interest Keywords
            smarter_filtered_articles = []
            for article_entry in fresh_articles:
                headline = article_entry.get('title', 'No Headline')
                summary = article_entry.get('summary', '') if hasattr(article_entry, 'summary') else ''

                # Combine headline and summary for comprehensive checking
                text_to_check = f"{headline.lower()} {summary.lower()}"

                is_high_priority = False
                for keyword in HIGH_PRIORITY_KEYWORDS:
                    if keyword.lower() in text_to_check:
                        is_high_priority = True
                        break # Found a high-priority keyword, no need to check others

                is_general_interest = False
                if not is_high_priority: # Only check general interest if not already high priority
                    for keyword in GENERAL_INTEREST_KEYWORDS:
                        if keyword.lower() in text_to_check:
                            is_general_interest = True
                            break # Found a general interest keyword

                # Article is included if it contains any high-priority keyword OR any general interest keyword
                if is_high_priority or is_general_interest:
                    smarter_filtered_articles.append(article_entry)
                else:
                    logging.info(f"News filtered out (no relevant keywords): '{headline[:80]}...'")

            # Assign the smartly filtered articles for subsequent processing
            keyword_filtered_articles = smarter_filtered_articles
            keyword_filtered_articles.sort(key=lambda x: x.unix_timestamp) # Ensure this sort remains after filtering

            if keyword_filtered_articles:
                logging.info(f"Found {len(keyword_filtered_articles)} new articles from FinancialJuice RSS.")
                new_latest_timestamp = last_timestamp

                for article_entry in keyword_filtered_articles:
                    article_timestamp = article_entry.unix_timestamp
                    english_headline = article_entry.get('title', 'No Headline')
                    
                    if english_headline.lower().startswith("financialjuice:"):
                        english_headline = english_headline[len("financialjuice:"):].strip()
                    
                    logging.info(f"Processing news: English Headline: {english_headline}")

                    # --- Call LibreTranslate for translation ---
                    somali_headline = await translate_text_libretranslate(english_headline, target_language="som")
                    # If translation fails, somali_headline will be the original English headline
                    # --- END LibreTranslate ---

                    # Use the translated headline in the Telegram message
                    telegram_message = (
                        f"🔴<b>DEGDEG:</b> {somali_headline}\n\n" # Use translated headline
                        f"Source: Hagarlaawe" # Fixed to include source name
                    )

                    await send_telegram_message(telegram_message)
                    new_latest_timestamp = max(new_latest_timestamp, article_timestamp) 

                if new_latest_timestamp > last_timestamp:
                    save_last_processed_timestamp(new_latest_timestamp)
                    logging.info(f"Updated last processed news timestamp to: {new_latest_timestamp}")
            else:
                logging.info("No new articles found since last check.")
        elif news_entries is not None:
            logging.warning("RSS feed returned no entries or unexpected data format.")
        else:
            logging.error("Failed to fetch news from RSS feed (data is None).")

        logging.info("Sleeping for 60 seconds for next check...")
        time.sleep(60)

if __name__ == "__main__":
    required_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "LIBRETRANSLATE_API_URL"] # Added LibreTranslate URL
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set them in Render's environment settings.")
        exit(1)

    import asyncio
    asyncio.run(main_loop())
