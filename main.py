import os
import requests
import json
import time
from datetime import datetime
import logging
from telegram import Bot
from telegram.error import TelegramError
import pytz # Import the pytz library for timezone handling
import feedparser # Import the feedparser library
import calendar # For converting parsed time to Unix timestamp

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration (from Environment Variables) ---
# FINNHUB_API_KEY is no longer needed for RSS feed
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# --- Constants ---
# Path for storing the last processed news timestamp
LAST_PROCESSED_TIMESTAMP_FILE = "last_processed_timestamp.txt"

# !!! ACTUAL FINANCIALJUICE RSS FEED URL !!!
FINANCIALJUICE_RSS_FEED_URL = "https://www.financialjuice.com/feed.ashx?xy=rss" # <--- This has been updated

# Define your interest keywords here (case-insensitive search will be applied)
INTEREST_KEYWORDS = [
    "usd", "eur", "jpy", "gbp", "cad", "aud", "nzd", "chf", # G8 Currencies
    "economic data", "inflation", "gdp", "unemployment", "interest rates", # Economic Data
    "central bank", "fed", "ecb", "boj", "boe", "rba", "rbnz", "snb", "boc", # Central Banks
    "btc", "eth", "bitcoin", "ethereum", "crypto", "cryptocurrency", # BTC & ETH News
    "gold", "xau", # Gold News
    "federal reserve", "secretary of finance", "treasury secretary", # Financial Political
    "china us trade", "tariff", "trade war", "sanctions", "trade talks", "trade deal", # China-US Trade/Tariffss
    "markets", "stocks", "bonds", "commodities", "forex" # General market terms to catch broader news
]

# Define East Africa Timezone
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
        # Disable web page preview to make the message cleaner by default
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message, parse_mode='HTML', disable_web_page_preview=True)
        logging.info("Message sent to Telegram.")
    except TelegramError as e:
        logging.error(f"Error sending message to Telegram: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while sending Telegram message: {e}")

async def main_loop():
    """Main loop to fetch and send news."""
    logging.info("Bot started. Entering main loop...")
    
    # Send a startup message to Telegram for debugging purposes
    try:
        bot_telegram_startup = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot_telegram_startup.send_message(chat_id=TELEGRAM_CHANNEL_ID, text="Bot has started and is checking for new financial news from FinancialJuice RSS. Posts will be filtered by topics and timestamps are in EAT.", parse_mode='HTML')
        logging.info("Startup message sent to Telegram.")
    except TelegramError as e:
        logging.warning(f"Could not send startup message to Telegram (this might be fine if channel is not ready or ID is not fully correct): {e}")

    while True:
        last_timestamp = get_last_processed_timestamp()
        
        news_entries = fetch_latest_news_from_rss()

        if news_entries and isinstance(news_entries, list):
            # 1. Filter for new articles (those with a timestamp greater than the last processed)
            fresh_articles = []
            for entry in news_entries:
                # Use entry.published_parsed for the timestamp, convert to Unix epoch
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    # Convert time.struct_time (which is UTC from RSS generally) to Unix timestamp
                    article_timestamp = calendar.timegm(entry.published_parsed)
                else:
                    # Fallback if published_parsed is missing, use current time
                    article_timestamp = int(time.time())
                    logging.warning(f"RSS entry '{entry.title if hasattr(entry, 'title') else 'No title'}' missing published date. Using current time.")

                if article_timestamp > last_timestamp:
                    entry.unix_timestamp = article_timestamp # Add unix_timestamp to entry for sorting/comparison
                    fresh_articles.append(entry)
            
            # 2. Filter these fresh articles by INTEREST_KEYWORDS
            keyword_filtered_articles = []
            for article_entry in fresh_articles:
                headline = article_entry.get('title', '').lower() # RSS entry title
                summary = article_entry.get('summary', '').lower() if hasattr(article_entry, 'summary') else '' # RSS entry summary

                found_keyword = False
                for keyword in INTEREST_KEYWORDS:
                    if keyword.lower() in headline or keyword.lower() in summary:
                        found_keyword = True
                        break # Found a keyword, no need to check others for this article
                
                if found_keyword:
                    keyword_filtered_articles.append(article_entry)

            # Sort the filtered articles by datetime (Unix timestamp) in ascending order to process oldest first
            keyword_filtered_articles.sort(key=lambda x: x.unix_timestamp)

            if keyword_filtered_articles:
                logging.info(f"Found {len(keyword_filtered_articles)} new articles matching your criteria from FinancialJuice RSS.")
                new_latest_timestamp = last_timestamp # Initialize with current last_timestamp

                for article_entry in keyword_filtered_articles:
                    article_timestamp = article_entry.unix_timestamp
                    headline = article_entry.get('title', 'No Headline')
                    # Source for RSS is often the feed's title, or implicit
                    source = "FinancialJuice" 
                    # url = article_entry.get('link', '#') # URL no longer used as per "no read more"
                    
                    logging.info(f"Processing news: Headline: {headline}")

                    # Format message for Telegram (English Only - Headline only, EAT Time)
                    # Convert Unix timestamp to datetime object, then to EAT
                    dt_object_utc = datetime.fromtimestamp(article_timestamp, tz=pytz.utc)
                    dt_object_eat = dt_object_utc.astimezone(EAST_AFRICA_TIMEZONE)
                    formatted_time_eat = dt_object_eat.strftime('%Y-%m-%d %H:%M EAT')

                    telegram_message = (
                        f"<b>FinancialJuice News Update</b>\n"
                        f"<b>Headline:</b> {headline}\n\n"
                        f"Source: {source}\n"
                        f"Time: {formatted_time_eat}"
                    )

                    await send_telegram_message(telegram_message)
                    # Update new_latest_timestamp with the highest timestamp processed in this batch
                    new_latest_timestamp = max(new_latest_timestamp, article_timestamp) 

                # Only save if we actually processed new articles and advanced the timestamp
                if new_latest_timestamp > last_timestamp:
                    save_last_processed_timestamp(new_latest_timestamp)
                    logging.info(f"Updated last processed news timestamp to: {new_latest_timestamp}")
            else:
                logging.info("No new articles matching your criteria found since last check.")
        elif news_entries is not None:
            logging.warning("RSS feed returned no entries or unexpected data format.")
        else:
            logging.error("Failed to fetch news from RSS feed (data is None).")

        logging.info("Sleeping for 60 seconds for next check...")
        time.sleep(60) # Sleep for 60 seconds (1 minute)

if __name__ == "__main__":
    # Ensure all critical environment variables are set before starting
    required_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID"]
    # Removed FINNHUB_API_KEY from required_vars as it's no longer used
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set them in Render's environment settings.")
        exit(1) # Exit if critical variables are missing

    # Initialize and run the bot using asyncio for Telegram's async methods
    import asyncio
    asyncio.run(main_loop())
