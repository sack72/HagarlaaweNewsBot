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
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TWITTER_USER_ID = os.getenv("TWITTER_USER_ID") # User ID for @financialjuice is 381696140
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# --- Constants ---
# Path for storing the last processed tweet ID (Render's filesystem is ephemeral)
LAST_TWEET_ID_FILE = "last_tweet_id.txt"
# URL for Twitter API v2 user tweets timeline
TWITTER_API_URL = f"https://api.twitter.com/2/users/{TWITTER_USER_ID}/tweets"

# Headers for Twitter API requests
TWITTER_HEADERS = {
    "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"
}

# --- Functions ---

def get_last_processed_tweet_id():
    """Retrieves the last processed tweet ID from a file or environment variable."""
    if os.path.exists(LAST_TWEET_ID_FILE):
        with open(LAST_TWEET_ID_FILE, "r") as f:
            return f.read().strip()
    elif os.getenv("LAST_PROCESSED_TWEET_ID"):
        return os.getenv("LAST_PROCESSED_TWEET_ID")
    return None

def save_last_processed_tweet_id(tweet_id):
    """Saves the last processed tweet ID to a file."""
    with open(LAST_TWEET_ID_FILE, "w") as f:
        f.write(str(tweet_id))

def fetch_latest_tweets(since_id=None):
    """Fetches latest tweets from the specified user."""
    params = {
        "tweet.fields": "created_at",
        "max_results": 5  # Fetch a small number to avoid overwhelming API/OpenAI
    }
    if since_id:
        params["since_id"] = since_id
    
    logging.info(f"Fetching tweets from user ID {TWITTER_USER_ID} since ID {since_id if since_id else 'None'}...")
    try:
        response = requests.get(TWITTER_API_URL, headers=TWITTER_HEADERS, params=params)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching tweets: {e}")
        time.sleep(15)
        logging.error(f"Response content: {response.text if response else 'N/A'}")
        return None

def translate_text_with_openai(text):
    """Translates text to Somali using OpenAI's GPT model."""
    if not OPENAI_API_KEY:
        logging.error("OpenAI API Key not set. Cannot translate.")
        return "Translation service unavailable."

    try:
        # Using the new OpenAI client
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that translates financial news to clear and concise Somali. Provide only the translated text, without any additional remarks or conversational filler. If the text is not financial news, just translate it as is."},
                {"role": "user", "content": f"Translate this financial news to Somali: {text}"}
            ],
            max_tokens=200
        )
        # Accessing the message content correctly
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Error translating with OpenAI: {e}")
        return f"Translation failed: {e}"

async def send_telegram_message(message):
    """Sends a message to the Telegram channel."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram Bot Token or Channel ID not set. Cannot send message.")
        return

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message, parse_mode='HTML')
        logging.info("Message sent to Telegram.")
    except TelegramError as e:
        logging.error(f"Error sending message to Telegram: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while sending Telegram message: {e}")

async def main_loop():
    """Main loop to fetch, translate, and send tweets."""
    logging.info("Bot started. Entering main loop...")
    bot_telegram = Bot(token=TELEGRAM_BOT_TOKEN) # Initialize bot once for updates
    
    while True:
        last_id = get_last_processed_tweet_id()
        data = fetch_latest_tweets(since_id=last_id)

        if data and 'data' in data:
            tweets = sorted(data['data'], key=lambda x: int(x['id'])) # Sort to process oldest first
            new_last_id = last_id

            for tweet in tweets:
                tweet_id = tweet['id']
                tweet_text = tweet['text']
                
                logging.info(f"Processing tweet ID: {tweet_id}")
                logging.info(f"Original text: {tweet_text}")

                translated_text = translate_text_with_openai(tweet_text)
                
                # Format message for Telegram
                telegram_message = (
                    f"**Original (English):**\n`{tweet_text}`\n\n"
                    f"**Turjumid (Somali):**\n`{translated_text}`\n\n"
                    f"_[Source Tweet](https://twitter.com/{os.getenv('TWITTER_USERNAME', 'financialjuice')}/status/{tweet_id})_"
                    # Note: We use TWITTER_USERNAME env var here, or default to financialjuice
                    # You might need to add TWITTER_USERNAME env var if you want it dynamic in link
                )

                await send_telegram_message(telegram_message)
                new_last_id = tweet_id # Update ID after successful processing

            if new_last_id and new_last_id != last_id:
                save_last_processed_tweet_id(new_last_id)
                logging.info(f"Updated last processed tweet ID to: {new_last_id}")
        elif data is not None:
            logging.info("No new tweets to process or 'data' field missing.")
        else:
            logging.error("Failed to fetch tweets (data is None).")

        logging.info("Sleeping for 5 minutes...")
        time.sleep(300) # Sleep for 5 minutes

if __name__ == "__main__":
    # Ensure all critical environment variables are set before starting
    required_vars = ["TWITTER_BEARER_TOKEN", "TWITTER_USER_ID", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set them in Render's environment settings.")
        exit(1) # Exit if critical variables are missing

    # Initialize and run the bot using asyncio for Telegram's async methods
    import asyncio
    asyncio.run(main_loop())
