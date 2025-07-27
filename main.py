import feedparser
from telegram import Bot
from googletrans import Translator
import time
import os
import pytz 
import requests 
import asyncio 
import re # Import the re module for regular expressions

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@HagarlaaweMarkets") 
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL", "YOUR_FINANCIAL_JUICE_RSS_FEED_URL") 

# --- Persistent Storage Configuration ---
# This path should point to a directory on your Render Persistent Disk.
# A common mount point is /var/data
PERSISTENT_STORAGE_PATH = "/var/data"
LAST_LINK_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")

# --- Global Variables ---
translator = Translator() 
last_posted_link = None 

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

# --- List of Somali prefixes to remove from translation (kept for robustness, though translation won't be displayed) ---
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

# --- New Functions for Persistence ---
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


# --- Main Bot Logic Functions ---

async def fetch_and_post_headlines():
    """
    Fetches new headlines from the RSS feed, filters them by keywords,
    and posts them to the Telegram channel (English only, no flags).
    """
    global last_posted_link 

    current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    print(f"[{current_time_str}] Checking RSS feed from: {FINANCIAL_JUICE_RSS_FEED_URL}")
    feed = feedparser.parse(FINANCIAL_JUICE_RSS_FEED_URL)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    new_entries_to_process = []
    
    # Process from newest to oldest to correctly identify the "last posted"
    # and stop early if we hit an already-posted item.
    for entry in feed.entries:
        if last_posted_link and entry.link == last_posted_link:
            print(f"Reached last posted link: {last_posted_link}. Stopping.")
            break
        new_entries_to_process.append(entry)
    
    # Reverse to process oldest new entries first, maintaining chronological order
    new_entries_to_process.reverse() 

    if not new_entries_to_process:
        print("No new headlines to post.")
        return

    print(f"Found {len(new_entries_to_process)} new headlines. Applying filters...")

    filtered_headlines_count = 0
    for entry in new_entries_to_process:
        english_headline_raw = entry.title 
        
        cleaned_english_headline = english_headline_raw.replace("FinancialJuice:", "").replace("Abuurjuice:", "").strip()

        if not contains_keywords(cleaned_english_headline, KEYWORDS):
            print(f"Skipping (no keywords): '{cleaned_english_headline}'")
            continue 
        
        print(f"Processing (contains keywords): '{cleaned_english_headline}'")
        filtered_headlines_count += 1

        try:
            # Translation part (still included but not displayed)
            translated_text_obj = translator.translate(cleaned_english_headline, dest='so') 
            somali_headline = translated_text_obj.text
            
            for prefix in SOMALI_PREFIXES_TO_REMOVE:
                if somali_headline.startswith(prefix):
                    somali_headline = somali_headline[len(prefix):].strip()
            somali_headline = somali_headline.strip()

            message_to_send = (
                f"**DEGDEG 🔴**\n\n" 
                f"{remove_flag_emojis(cleaned_english_headline)}" 
            )
            
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode='Markdown', 
                disable_web_page_preview=True 
            )
            print(f"Posted: '{cleaned_english_headline}'")
            
            # --- IMPORTANT: Update and save last_posted_link after successful send ---
            last_posted_link = entry.link 
            save_last_posted_link(last_posted_link)
            
            await asyncio.sleep(1) 

        except Exception as e:
            print(f"Error processing or posting headline '{english_headline_raw}': {e}")
            try:
                fallback_message = (
                    f"**DEGDEG 🔴**\n\n" 
                    f"{remove_flag_emojis(cleaned_english_headline)}" 
                )
                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=fallback_message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                print(f"Posted original English due to error: '{cleaned_english_headline}'")
                # Even on fallback, if message was sent, update the link to prevent re-sending this specific item.
                last_posted_link = entry.link
                save_last_posted_link(last_posted_link)
            except Exception as inner_e:
                print(f"Failed to post even original English headline '{cleaned_english_headline}': {inner_e}")
    
    if filtered_headlines_count == 0 and len(new_entries_to_process) > 0:
        print("No new headlines matched the keyword filter.")


# --- Main Execution Loop ---
if __name__ == "__main__":
    print("Bot starting...")
    # --- Load last posted link at startup ---
    last_posted_link = load_last_posted_link()
    
    while True:
        asyncio.run(fetch_and_post_headlines()) 
        
        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) 
        print(f"[{current_time_str}] Sleeping for 1 minute...")
        time.sleep(60)

