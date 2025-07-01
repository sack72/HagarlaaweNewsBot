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

# --- NEW IMPORTS FOR OPENAI ---
from openai import OpenAI
from openai import APIError # Specific error for OpenAI API issues
# --- END NEW IMPORTS ---

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration (from Environment Variables) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # --- NEW: Get OpenAI API Key ---

# --- Constants ---
LAST_PROCESSED_TIMESTAMP_FILE = "last_processed_timestamp.txt"
FINANCIALJUICE_RSS_FEED_URL = "https://www.financialjuice.com/feed.ashx?xy=rss"

# Define your interest keywords here (case-insensitive search will be applied)
# Note: You currently have a line in your code that effectively removes this filter:
# "keyword_filtered_articles = fresh_articles"
# If you want to re-enable keyword filtering, you'll need to adjust that line.
INTEREST_KEYWORDS = [
    "usd", "eur", "jpy", "gbp", "cad", "aud", "nzd", "chf", # G8 Currencies
    "economic data", "inflation", "gdp", "unemployment", "interest rates", # Economic Data
    "central bank", "fed", "ecb", "boj", "boe", "rba", "rbnz", "snb", "boc", # Central Banks
    "btc", "eth", "bitcoin", "ethereum", "crypto", "cryptocurrency", # BTC & ETH News
    "gold", "xau", # Gold News
    "federal reserve", "secretary of finance", "treasury secretary", # Financial Political
    "china us trade", "tariff", "trade war", "sanctions", "trade talks", "trade deal", # China-US Trade/Tariffs
    "markets", "stocks", "bonds", "commodities", "forex" # General market terms to catch broader news
]

EAST_AFRICA_TIMEZONE = pytz.timezone('Africa/Nairobi')

# --- NEW: Initialize OpenAI Client ---
# Only initialize if the API key is present
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    logging.error("OPENAI_API_KEY not set. Translation functionality will be disabled.")
# --- END NEW ---

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

# --- Translation Function using OpenAI ---
async def translate_text_openai(text_to_translate, target_language="Somali"):
    """
    Translates text using the OpenAI Chat Completions API.
    Returns the translated text or original text on failure.
    """
    if not openai_client:
        logging.warning("OpenAI client not initialized (API key missing). Skipping translation.")
        return text_to_translate # Return original if API key is missing

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini", # **CONFIRMED MODEL TO USE**
            messages=[
                {"role": "system", "content": f"You are a helpful assistant that translates financial news headlines accurately and concisely into {target_language}. Maintain the serious tone of news and focus on financial terminology."},
                {"role": "user", "content": f"Translate the following English financial news headline into {target_language} and provide only the translated text, do not add anything else:\n\n'{text_to_translate}'"}
            ],
            temperature=0.7, # Controls randomness. Lower for more consistent results.
            max_tokens=100 # Max tokens for the response, sufficient for headlines
        )
        translated_text = response.choices[0].message.content.strip()
        logging.info(f"Translated '{text_to_translate}' to '{translated_text}'")
        return translated_text
    except APIError as e:
        logging.error(f"OpenAI API error during translation: {e}")
        return text_to_translate # Return original on API error
    except Exception as e:
        logging.error(f"An unexpected error occurred during OpenAI translation: {e}")
        return text_to_translate # Return original on other errors
# --- END NEW ---

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
            
            keyword_filtered_articles = fresh_articles
            keyword_filtered_articles.sort(key=lambda x: x.unix_timestamp)

            if keyword_filtered_articles:
                logging.info(f"Found {len(keyword_filtered_articles)} new articles from FinancialJuice RSS.")
                new_latest_timestamp = last_timestamp

                for article_entry in keyword_filtered_articles:
                    article_timestamp = article_entry.unix_timestamp
                    english_headline = article_entry.get('title', 'No Headline')
                    
                    if english_headline.lower().startswith("financialjuice:"):
                        english_headline = english_headline[len("financialjuice:"):].strip()
                    
                    logging.info(f"Processing news: English Headline: {english_headline}")

                    # --- Call OpenAI for translation ---
                    somali_headline = await translate_text_openai(english_headline, target_language="Somali")
                    # If translation fails, somali_headline will be the original English headline
                    # --- END NEW ---

                    # Use the translated headline in the Telegram message
                    telegram_message = (
                        f"🔴<b>DEGDEG:</b> {somali_headline}\n\n" # Use translated headline
                        f"Source:"
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
    required_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "OPENAI_API_KEY"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set them in Render's environment settings.")
        exit(1)

    import asyncio
    asyncio.run(main_loop())

