import feedparser
from telegram import Bot
from googletrans import Translator
import time
import os
import pytz 
import requests 
import asyncio 

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@HagarlaaweMarkets") 
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL", "YOUR_FINANCIAL_JUICE_RSS_FEED_URL") 

# --- Global Variables ---
translator = Translator()
last_posted_link = None 

# --- Keyword Filtering ---
# Keywords for major macroeconomic data, major banks, and major currencies
# These will be checked in a case-insensitive manner.
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

def contains_keywords(text, keywords):
    """Checks if the text contains any of the specified keywords (case-insensitive)."""
    text_lower = text.lower()
    for keyword in keywords:
        if keyword.lower() in text_lower:
            return True
    return False

# --- Functions ---

async def fetch_and_post_headlines():
    """
    Fetches new headlines from the RSS feed, filters them by keywords,
    translates them to Somali, and posts only the Somali version to Telegram.
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
        english_headline = entry.title
        post_url = entry.link # Keep URL for logging/debugging if needed

        # --- Keyword Filtering Logic ---
        if not contains_keywords(english_headline, KEYWORDS):
            print(f"Skipping (no keywords): '{english_headline}'")
            continue # Skip this headline if no keywords are found
        
        print(f"Processing (contains keywords): '{english_headline}'")
        filtered_headlines_count += 1

        try:
            await asyncio.sleep(0.5) # Delay for Google Translate calls
            
            translated_text_obj = await translator.translate(english_headline, dest='so') 
            somali_headline = translated_text_obj.text
            
            # --- Updated Message Format ---
            # Posts only the Somali version, with "DEGDEG" header, no FinancialJuice prefix, no "Read More"
            message_to_send = (
                f"**DEGDEG**\n\n"
                f"🇸🇴: {somali_headline}" 
            )
            
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode='Markdown', # Allows for bolding
                disable_web_page_preview=True 
            )
            print(f"Posted Somali: '{somali_headline}' (Original: '{english_headline}')")
            
            last_posted_link = entry.link 
            
            await asyncio.sleep(1) # Delay between Telegram messages

        except Exception as e:
            print(f"Error translating or posting headline '{english_headline}': {e}")
            # Fallback: if translation fails, post the original English version with an error note
            try:
                fallback_message = (
                    f"**DEGDEG (Translation Failed)**\n\n"
                    f"🇬🇧: {english_headline}"
                )
                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=fallback_message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                print(f"Posted original English due to translation error: '{english_headline}'")
            except Exception as inner_e:
                print(f"Failed to post even original English headline '{english_headline}': {inner_e}")
    
    if filtered_headlines_count == 0 and len(new_entries_to_process) > 0:
        print("No new headlines matched the keyword filter.")


# --- Main Execution Loop ---
if __name__ == "__main__":
    print("Bot starting...")
    
    while True:
        asyncio.run(fetch_and_post_headlines()) 
        
        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) 
        print(f"[{current_time_str}] Sleeping for 1 minute...")
        time.sleep(60) # Changed to 1 minute as requested

