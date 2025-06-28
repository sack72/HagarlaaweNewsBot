import os
import requests
import json
import time
from datetime import datetime
import logging
from telegram import Bot
from telegram.error import TelegramError
import pytz # Import the pytz library for timezone handling

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration (from Environment Variables) ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY") # API Key for Finnhub
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# --- Constants ---
# Path for storing the last processed news timestamp
LAST_PROCESSED_TIMESTAMP_FILE = "last_processed_timestamp.txt"
# URL for Finnhub general news API
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"

# Define your interest keywords here (case-insensitive search will be applied)
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

# Define desired news sources (case-sensitive as returned by Finnhub)
DESIRED_SOURCES = ["Bloomberg", "Reuters"] # <--- NEW: Customize this list

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

def fetch_latest_news_from_finnhub():
    """Fetches the latest general news from Finnhub."""
    params = {
        "token": FINNHUB_API_KEY,
        "category": "general" # Using 'general' to get a broad set of news for keyword filtering
    }
    
    logging.info("Fetching latest news from Finnhub...")
    try:
        response = requests.get(FINNHUB_NEWS_URL, params=params)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching news from Finnhub: {e}")
        logging.error(f"Response content: {response.text if response else 'N/A'}")
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
        await bot_telegram_startup.send_message(chat_id=TELEGRAM_CHANNEL_ID, text="Bot has started and is checking for new financial news from Finnhub. Posts will be filtered by topics and sources, and timestamps are in EAT.", parse_mode='HTML')
        logging.info("Startup message sent to Telegram.")
    except TelegramError as e:
        logging.warning(f"Could not send startup message to Telegram (this might be fine if channel is not ready or ID is not fully correct): {e}")

    while True:
        last_timestamp = get_last_processed_timestamp()
        
        news_data = fetch_latest_news_from_finnhub()

        if news_data and isinstance(news_data, list):
            # 1. Filter for new articles (those with a timestamp greater than the last processed)
            fresh_articles = [
                article for article in news_data
                if article.get('datetime', 0) > last_timestamp
            ]
            
            # 2. Filter these fresh articles by DESIRED_SOURCES
            source_filtered_articles = []
            for article in fresh_articles:
                source = article.get('source', '')
                if source in DESIRED_SOURCES:
                    source_filtered_articles.append(article)
            
            # 3. Filter these source-filtered articles by INTEREST_KEYWORDS
            keyword_and_source_filtered_articles = []
            for article in source_filtered_articles:
                headline = article.get('headline', '').lower() # Convert to lowercase for case-insensitive search
                summary = article.get('summary', '').lower()

                found_keyword = False
                for keyword in INTEREST_KEYWORDS:
                    if keyword.lower() in headline or keyword.lower() in summary:
                        found_keyword = True
                        break # Found a keyword, no need to check others for this article
                
                if found_keyword:
                    keyword_and_source_filtered_articles.append(article)

            # Sort the filtered articles by datetime (Unix timestamp) in ascending order to process oldest first
            keyword_and_source_filtered_articles.sort(key=lambda x: x.get('datetime', 0))

            if keyword_and_source_filtered_articles:
                logging.info(f"Found {len(keyword_and_source_filtered_articles)} new articles matching your criteria from Finnhub.")
                new_latest_timestamp = last_timestamp # Initialize with current last_timestamp

                for article in keyword_and_source_filtered_articles:
                    article_timestamp = article.get('datetime')
                    headline = article.get('headline', 'No Headline')
                    source = article.get('source', 'Unknown Source')
                    url = article.get('url', '#')
                    
                    logging.info(f"Processing news: Headline: {headline}")

                    # Format message for Telegram (English Only - Headline only, EAT Time)
                    # Convert Unix timestamp to datetime object, then to EAT
                    dt_object_utc = datetime.fromtimestamp(article_timestamp, tz=pytz.utc)
                    dt_object_eat = dt_object_utc.astimezone(EAST_AFRICA_TIMEZONE)
                    formatted_time_eat = dt_object_eat.strftime('%Y-%m-%d %H:%M EAT')

                    telegram_message = (
                        f"<b>Finnhub News Update</b>\n"
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
        elif news_data is not None:
            logging.warning("Finnhub API returned empty list or unexpected data format.")
        else:
            logging.error("Failed to fetch news from Finnhub (data is None).")

        logging.info("Sleeping for 60 seconds for next check...")
        time.sleep(60) # Sleep for 60 seconds (1 minute)

if __name__ == "__main__":
    # Ensure all critical environment variables are set before starting
    required_vars = ["FINNHUB_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set them in Render's environment settings.")
        exit(1) # Exit if critical variables are missing

    # Initialize and run the bot using asyncio for Telegram's async methods
    import asyncio
    asyncio.run(main_loop())
