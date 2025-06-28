import os
import requests
import json
import time
from datetime import datetime, timezone
import logging
from telegram import Bot
from telegram.error import TelegramError

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
        "category": "general" # Can be 'general', 'forex', 'crypto', etc.
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
        await bot_telegram_startup.send_message(chat_id=TELEGRAM_CHANNEL_ID, text="Bot has started and is checking for new financial news from Finnhub.", parse_mode='HTML')
        logging.info("Startup message sent to Telegram.")
    except TelegramError as e:
        logging.warning(f"Could not send startup message to Telegram (this might be fine if channel is not ready or ID is not fully correct): {e}")

    while True:
        last_timestamp = get_last_processed_timestamp()
        
        # Finnhub general news endpoint provides the latest news,
        # we'll filter by timestamp client-side to find truly new articles.
        news_data = fetch_latest_news_from_finnhub()

        if news_data and isinstance(news_data, list): # Finnhub returns a list of news articles
            # Filter for new articles (those with a timestamp greater than the last processed)
            new_articles = [
                article for article in news_data
                if article.get('datetime', 0) > last_timestamp
            ]
            
            # Sort new_articles by datetime (Unix timestamp) in ascending order to process oldest first
            new_articles.sort(key=lambda x: x.get('datetime', 0))

            if new_articles:
                logging.info(f"Found {len(new_articles)} new articles from Finnhub.")
                new_latest_timestamp = last_timestamp # Initialize with current last_timestamp

                for article in new_articles:
                    article_timestamp = article.get('datetime')
                    headline = article.get('headline', 'No Headline')
                    summary = article.get('summary', 'No Summary')
                    source = article.get('source', 'Unknown Source')
                    url = article.get('url', '#')
                    
                    logging.info(f"Processing news: Headline: {headline}")

                    # --- Removed Gemini translation here ---
                    
                    # Format message for Telegram (English Only)
                    # Convert Unix timestamp to a readable datetime string (e.g., YYYY-MM-DD HH:MM UTC)
                    dt_object = datetime.fromtimestamp(article_timestamp, tz=timezone.utc)
                    formatted_time = dt_object.strftime('%Y-%m-%d %H:%M UTC')

                    telegram_message = (
                        f"**Finnhub News Update**\n"
                        f"**Headline:** `{headline}`\n\n"
                        f"**Summary:** `{summary}`\n\n"
                        f"Source: {source}\n"
                        f"Time: {formatted_time}\n"
                        f"Full story: <a href='{url}'>Read More</a>"
                    )

                    await send_telegram_message(telegram_message)
                    # Update new_latest_timestamp with the highest timestamp processed in this batch
                    new_latest_timestamp = max(new_latest_timestamp, article_timestamp) 

                # Only save if we actually processed new articles and advanced the timestamp
                if new_latest_timestamp > last_timestamp:
                    save_last_processed_timestamp(new_latest_timestamp)
                    logging.info(f"Updated last processed news timestamp to: {new_latest_timestamp}")
            else:
                logging.info("No new articles found from Finnhub since last check.")
        elif news_data is not None:
            logging.warning("Finnhub API returned empty list or unexpected data format.")
        else:
            logging.error("Failed to fetch news from Finnhub (data is None).")

        logging.info("Sleeping for 1 hour...")
        time.sleep(3600) # Sleep for 1 hour (3600 seconds)

if __name__ == "__main__":
    # Ensure all critical environment variables are set before starting
    required_vars = ["FINNHUB_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID"] # GEMINI_API_KEY removed
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set them in Render's environment settings.")
        exit(1) # Exit if critical variables are missing

    # Initialize and run the bot using asyncio for Telegram's async methods
    import asyncio
    asyncio.run(main_loop())
