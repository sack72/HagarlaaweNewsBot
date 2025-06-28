import os
import requests
import json
import time
from datetime import datetime, timezone
import logging
from telegram import Bot
from telegram.error import TelegramError

# Import the Google Generative AI library
import google.generativeai as genai

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration (from Environment Variables) ---
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TWITTER_USER_ID = os.getenv("TWITTER_USER_ID") # User ID for @financialjuice is 381696140
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # Changed from OPENAI_API_KEY
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# --- Initialize Gemini (using your new API key) ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Optional: Choose a model, e.g., "gemini-pro" for text-only tasks
    gemini_model = genai.GenerativeModel('gemini-pro')
else:
    logging.error("GEMINI_API_KEY not set. Translation service will not work.")
    gemini_model = None


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
        logging.error(f"Response content: {response.text if response else 'N/A'}")
        return None

def translate_text_with_gemini(text):
    """Translates text to Somali using Google Gemini's GPT model."""
    if not gemini_model:
        logging.error("Gemini model not initialized. Cannot translate.")
        return "Translation service unavailable."

    try:
        # Construct the prompt for Gemini
        prompt_parts = [
            {"text": "You are a helpful assistant that translates financial news to clear and concise Somali. Provide only the translated text, without any additional remarks or conversational filler. If the text is not financial news, just translate it as is."},
            {"text": f"Translate this financial news to Somali: {text}"}
        ]
        
        response = gemini_model.generate_content(prompt_parts)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Error translating with Gemini: {e}")
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
    
    # Send a startup message to Telegram for debugging purposes
    try:
        bot_telegram_startup = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot_telegram_startup.send_message(chat_id=TELEGRAM_CHANNEL_ID, text="Bot has started and is checking for new tweets.", parse_mode='HTML')
        logging.info("Startup message sent to Telegram.")
    except TelegramError as e:
        logging.warning(f"Could not send startup message to Telegram (this might be fine if channel is not ready): {e}")

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

                # Use Gemini for translation
                translated_text = translate_text_with_gemini(tweet_text)
                
                # Format message for Telegram
                telegram_message = (
                    f"**Original (English):**\n`{tweet_text}`\n\n"
                    f"**Turjumid (Somali):**\n`{translated_text}`\n\n"
                    f"_[Source Tweet](https://twitter.com/{os.getenv('TWITTER_USERNAME', 'financialjuice')}/status/{tweet_id})_"
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
        time.sleep(3600) # Sleep for 60 minutes

if __name__ == "__main__":
    # Ensure all critical environment variables are set before starting
    required_vars = ["TWITTER_BEARER_TOKEN", "TWITTER_USER_ID", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set them in Render's environment settings.")
        exit(1) # Exit if critical variables are missing

    # Initialize and run the bot using asyncio for Telegram's async methods
    import asyncio
    asyncio.run(main_loop())
