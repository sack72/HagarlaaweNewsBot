import os
import time
import re # Import the re module for regular expressions
import asyncio

import feedparser
import pytz
import requests # Although 'requests' is in requirements, it's not directly used in the provided code snippet but good to keep if used elsewhere.
from telegram import Bot # Keep this for initializing the bot
import openai # ADD: Import the OpenAI library

# --- Configuration ---
# IMPORTANT: Set these as environment variables in Render for security and easy management!
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE") # Replace with your default or ensure env var is set
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@HagarlaaweMarkets") # Replace with your default or ensure env var is set
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL", "YOUR_FINANCIAL_JUICE_RSS_FEED_URL_HERE") # Replace with your default or ensure env var is set

# --- OpenAI API Key Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # Load OpenAI API key from environment variables
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable not set. Please set it in Render Dashboard.")
openai.api_key = OPENAI_API_KEY # Initialize the OpenAI client

# --- Persistent Storage Configuration ---
# This path should point to a directory on your Render Persistent Disk.
# A common mount point is /var/data
PERSISTENT_STORAGE_PATH = "/var/data"
LAST_LINK_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")

# --- Global Variables ---
last_posted_link = None # This will be loaded from file at startup

# --- Keyword Filtering ---
KEYWORDS = [
    # Macroeconomics Data
    "cpi", "inflation", "gdp", "jobs report", "non-farm payrolls", "nfp",
    "interest rate", "fed", "central bank", "ecb", "boe", "boj", "fomc",
    "rate hike", "recession", "unemployment", "manufacturing pmi",
    "services pmi", "trade balance", "retail sales", "consumer confidence",
    "economic outlook", "fiscal policy", "monetary policy", "yields",

    # Major Banks (including central banks by name)
    "jpmorgan", "goldman sachs", "bank of america", "citi", "wells fargo",
    "hsbc", "barclays", "deutsche bank", "ubs", "federal reserve",
    "european central bank", "bank of england", "bank of japan", "imf",
    "world bank", "moody's", "s&p", "fitch", "bank of international settlements",

    # Major Currencies
    "usd", "eur", "jpy", "gbp", "chf", "cad", "aud", "nzd", "yen", "pound",
    "euro", "dollar", "currency", "forex", "fx", "greenback",
]

# --- List of Somali prefixes to remove from translation (can be kept for robustness) ---
SOMALI_PREFIXES_TO_REMOVE = [
    "Qeybta Abaalmarinta:",
    "Qeyb-qabad:",
    "Qeyb-dhaqameedka",
    "Qeyb-dhaqaale:",
    "Fieldinice:",
    "Fieldjuice:",
    "Dhaqaale:",
    "Abuurjuice:",
    # Add any other new prefixes you might find here
]

def contains_keywords(text, keywords):
    """Checks if the text contains any of the specified keywords (case-insensitive)."""
    text_lower = text.lower()
    for keyword in keywords:
        if keyword.lower() in text_lower:
            return True
    return False

def remove_flag_emojis(text):
    """
    Removes common flag emojis (regional indicator symbol pairs)
    and their associated colons/whitespace from the text.
    """
    flag_pattern = r'[\U0001F1E6-\U0001F1FF]{2}:?\s*'
    cleaned_text = re.sub(flag_pattern, '', text, flags=re.UNICODE)
    return cleaned_text.strip()

# --- Functions for Persistence ---
def load_last_posted_link():
    """Loads the last posted link from the persistent file."""
    if os.path.exists(LAST_LINK_FILE):
        try:
            with open(LAST_LINK_FILE, 'r') as f:
                link = f.readline().strip()
                print(f"Loaded last_posted_link: {link}")
                return link if link else None
        except Exception as e:
            print(f"Error loading last_posted_link from file: {e}")
            return None
    print("No last_posted_link file found.")
    return None

def save_last_posted_link(link):
    """Saves the last posted link to the persistent file."""
    try:
        # Ensure the directory exists
        os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
        with open(LAST_LINK_FILE, 'w') as f:
            f.write(link)
        print(f"Saved last_posted_link: {link}")
    except Exception as e:
        print(f"Error saving last_posted_link to file: {e}")

# --- ADD: OpenAI Translation Function ---
async def translate_text_with_gpt(text: str, target_language: str = "Somali") -> str:
    """
    Translates the given English text to the target language using OpenAI GPT.
    """
    try:
        # Use openai.chat.completions.create for chat-optimized models
        response = await openai.chat.completions.create(
            model="gpt-3.5-turbo",  # Consider "gpt-4" for higher quality, but higher cost
            messages=[
                {"role": "system", "content": f"You are a highly accurate and professional translator. Translate the following English financial news text into {target_language}. Maintain the original meaning, tone, and format."},
                {"role": "user", "content": text}
            ],
            temperature=0.3, # Lower temperature for more deterministic and literal translation (0.2-0.7 is good range)
            max_tokens=1000 # Max tokens for the output. Adjust if headlines are very long.
        )
        translated_text = response.choices[0].message.content.strip()
        return translated_text
    except openai.APIError as e:
        print(f"OpenAI API Error during translation: {e}")
        # Log the error and return original text or a friendly error message
        return f"Translation service currently unavailable due to API error. Original text: {text}"
    except Exception as e:
        print(f"An unexpected error occurred during translation: {e}")
        return f"Translation failed due to an internal error. Original text: {text}"

# --- Main Bot Logic Functions ---

async def fetch_and_post_headlines():
    """
    Fetches new headlines from the RSS feed, filters them by keywords,
    translates them to Somali via GPT API, and posts them to the Telegram channel.
    """
    global last_posted_link # Declare global to modify the variable

    current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    print(f"[{current_time_str}] Checking RSS feed from: {FINANCIAL_JUICE_RSS_FEED_URL}")
    feed = feedparser.parse(FINANCIAL_JUICE_RSS_FEED_URL)
    
    # Initialize bot inside the async function if you're not using PTB's Application builder
    # or ensure it's passed as an argument if it's managed centrally.
    # For a simple script, initializing here is fine.
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    new_entries_to_process = []

    # Process from newest to oldest to correctly identify the "last posted"
    # and stop early if we hit an already-posted item.
    for entry in feed.entries:
        # Check if the entry has a link before comparing
        if hasattr(entry, 'link') and entry.link == last_posted_link:
            print(f"Reached last posted link: {last_posted_link}. Stopping.")
            break
        new_entries_to_process.append(entry)

    # Reverse to process oldest new entries first, maintaining chronological order
    new_entries_to_process.reverse()

    if not new_entries_to_process:
        print("No new headlines to post.")
        return

    print(f"Found {len(new_entries_to_process)} new headlines. Applying filters and translating...")

    filtered_headlines_count = 0
    for entry in new_entries_to_process:
        english_headline_raw = entry.title
        link = entry.link if hasattr(entry, 'link') else None # Ensure link exists

        cleaned_english_headline = english_headline_raw.replace("FinancialJuice:", "").replace("Abuurjuice:", "").strip()

        # Remove flag emojis from the English headline before keyword check and translation
        cleaned_english_headline = remove_flag_emojis(cleaned_english_headline)

        if not contains_keywords(cleaned_english_headline, KEYWORDS):
            print(f"Skipping (no keywords): '{cleaned_english_headline}'")
            continue

        print(f"Processing (contains keywords): '{cleaned_english_headline}'")
        filtered_headlines_count += 1

        try:
            # --- THIS IS THE KEY OpenAI Translation Call ---
            somali_headline = await translate_text_with_gpt(cleaned_english_headline, "Somali")

            # Apply Somali prefix removal (less needed with GPT, but can be kept)
            for prefix in SOMALI_PREFIXES_TO_REMOVE:
                if somali_headline.startswith(prefix):
                    somali_headline = somali_headline[len(prefix):].strip()
            somali_headline = somali_headline.strip()

            # Construct the message for Telegram
            # It will include both original English and Translated Somali
            message_to_send = (
                f"**DEGDEG 🔴**\n\n"
                f"*{cleaned_english_headline}*\n\n" # Original English in italics
                f"{somali_headline}" # Translated Somali
            )
            
            # Optionally add the link if available
            if link:
                message_to_send += f"\n\n[Read more]({link})"

            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode='Markdown', # Allows bold, italics, etc.
                disable_web_page_preview=True # Prevents Telegram from showing link previews
            )
            print(f"Posted translated: '{cleaned_english_headline}'")

            # --- IMPORTANT: Update and save last_posted_link after successful send ---
            if link: # Only save if a link was successfully processed and sent
                last_posted_link = link
                save_last_posted_link(last_posted_link)

            await asyncio.sleep(1) # Small delay to avoid hitting Telegram rate limits

        except Exception as e:
            print(f"Error processing or posting headline '{english_headline_raw}': {e}")
            try:
                # Fallback to sending original English if translation or posting fails
                fallback_message = (
                    f"**DEGDEG 🔴 (Translation Error)**\n\n"
                    f"{cleaned_english_headline}"
                )
                if link:
                    fallback_message += f"\n\n[Read more]({link})"

                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=fallback_message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                print(f"Posted original English due to error: '{cleaned_english_headline}'")
                # Even on fallback, if message was sent, update the link to prevent re-sending this specific item.
                if link:
                    last_posted_link = link
                    save_last_posted_link(last_posted_link)
            except Exception as inner_e:
                print(f"Failed to post even original English headline '{cleaned_english_headline}': {inner_e}")

    if filtered_headlines_count == 0 and len(new_entries_to_process) > 0:
        print("No new headlines matched the keyword filter.")


# --- Main Execution Loop ---
if __name__ == "__main__":
    print("Bot starting...")
    # --- Create the persistent storage directory if it doesn't exist at startup ---
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    print(f"Persistent storage path ensured: {PERSISTENT_STORAGE_PATH}")
    
    # --- Load last posted link at startup ---
    last_posted_link = load_last_posted_link()

    # Run the main loop
    while True:
        try:
            asyncio.run(fetch_and_post_headlines())
        except Exception as e:
            print(f"An error occurred in the main fetch loop: {e}")
            # Consider adding a longer sleep here if persistent errors occur, e.g., time.sleep(300)

        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        print(f"[{current_time_str}] Sleeping for 1 minute before next check...")
        time.sleep(60) # Wait for 60 seconds (1 minute) before the next fetch
