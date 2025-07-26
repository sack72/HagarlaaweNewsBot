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

# --- Global Variables ---
# The googletrans Translator is not truly async, but it can be used with await due to its internal workings.
# For truly robust async, consider an async translation library if performance issues arise.
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
    """Removes common flag emojis and their associated colons from the text."""
    # This regex matches common flag emojis and the colon that often follows them.
    # You can expand this if you notice other flags appearing.
    # It also handles the case where the flag might be at the start of the string.
    return re.sub(r'(\ud83c[\udde6-\ud83d\udeff]|\ud83c[\ude00-\udeff])+:?\s*', '', text, flags=re.UNICODE)


# --- Functions ---

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
    
    for entry in feed.entries:
        if entry.link == last_posted_link:
            break
        new_entries_to_process.append(entry)
    
    new_entries_to_process.reverse() 

    if not new_entries_to_process:
        print("No new headlines to post.")
        return

    print(f"Found {len(new_entries_to_process)} new headlines. Applying filters...")

    filtered_headlines_count = 0
    for entry in new_entries_to_process:
        english_headline_raw = entry.title # Keep raw for potential error logging
        post_url = entry.link 

        # Clean the headline before keyword check (to avoid issues with prefixes interfering)
        cleaned_english_headline = english_headline_raw.replace("FinancialJuice:", "").replace("Abuurjuice:", "").strip()

        # --- Keyword Filtering Logic ---
        if not contains_keywords(cleaned_english_headline, KEYWORDS):
            print(f"Skipping (no keywords): '{cleaned_english_headline}'")
            continue 
        
        print(f"Processing (contains keywords): '{cleaned_english_headline}'")
        filtered_headlines_count += 1

        try:
            # Although we are not displaying Somali, we might still translate it if you have other uses.
            # If translation errors are common and you don't need the Somali, you can remove these lines entirely.
            translated_text_obj = await translator.translate(cleaned_english_headline, dest='so') 
            somali_headline = translated_text_obj.text
            
            # Clean Somali prefixes (still useful if you log the Somali translation, or re-enable it later)
            for prefix in SOMALI_PREFIXES_TO_REMOVE:
                if somali_headline.startswith(prefix):
                    somali_headline = somali_headline[len(prefix):].strip()
            somali_headline = somali_headline.strip() # Final strip for any remaining whitespace


            # --- Main Message Format (English only, no flags) ---
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
            print(f"Posted: '{cleaned_english_headline}'") # Log only English posted
            
            last_posted_link = entry.link 
            
            await asyncio.sleep(1) # Small delay to avoid hitting Telegram API limits

        except Exception as e:
            print(f"Error processing or posting headline '{english_headline_raw}': {e}")
            try:
                # Fallback message (English only, no flags, even in error case)
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
            except Exception as inner_e:
                print(f"Failed to post even original English headline '{cleaned_english_headline}': {inner_e}")
    
    if filtered_headlines_count == 0 and len(new_entries_to_process) > 0:
        print("No new headlines matched the keyword filter.")


# --- Main Execution Loop ---
if __name__ == "__main__":
    print("Bot starting...")
    
    while True:
        asyncio.run(fetch_and_post_headlines()) 
        
        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) 
        print(f"[{current_time_str}] Sleeping for 1 minute...")
        time.sleep(60)

