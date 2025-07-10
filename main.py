import feedparser
from telegram import Bot
from googletrans import Translator
import time
import os
import pytz 
import requests 
import asyncio # New: Import asyncio to run async functions

# --- Configuration ---
# Get these from your environment variables in Render!
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@HagarlaaweMarkets") 
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL", "YOUR_FINANCIAL_JUICE_RSS_FEED_URL") 

# --- Global Variables ---
translator = Translator() # The Translator object itself is often created synchronously
last_posted_link = None 

# --- Functions ---

# Change this function to be asynchronous
async def fetch_and_post_headlines(): # Added 'async' keyword
    """
    Fetches new headlines from the RSS feed, translates them to Somali,
    and posts them to the Telegram channel.
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

    print(f"Found {len(new_entries_to_process)} new headlines.")

    for entry in new_entries_to_process:
        english_headline = entry.title
        post_url = entry.link
        
        try:
            # Adding a small delay for googletrans calls to avoid hitting rate limits
            await asyncio.sleep(0.5) # Changed time.sleep to await asyncio.sleep
            
            # Await the translate call
            translated_text_obj = await translator.translate(english_headline, dest='so') 
            somali_headline = translated_text_obj.text
            
            message_to_send = (
                f"**HAGARLAAWE MARKETS NEWS**\n\n"
                f"🇬🇧: {english_headline}\n\n"
                f"🇸🇴: {somali_headline}\n\n"
                f"[Read More]({post_url})" 
            )
            
            # Await the send_message call
            await bot.send_message( # Added 'await' keyword
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode='Markdown', 
                disable_web_page_preview=True 
            )
            print(f"Posted: '{english_headline}' -> '{somali_headline}'")
            
            last_posted_link = entry.link 
            
            # Add a small delay between Telegram messages to avoid API rate limits
            await asyncio.sleep(1) # Changed time.sleep to await asyncio.sleep

        except Exception as e:
            print(f"Error translating or posting headline '{english_headline}': {e}")
            try:
                fallback_message = (
                    f"**HAGARLAAWE MARKETS NEWS (Translation Failed)**\n\n"
                    f"Original English:\n{english_headline}\n\n"
                    f"[Read More]({post_url})"
                )
                await bot.send_message( # Added 'await' keyword
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=fallback_message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                print(f"Posted original English due to translation error: '{english_headline}'")
            except Exception as inner_e:
                print(f"Failed to post even original English headline '{english_headline}': {inner_e}")


# --- Main Execution Loop ---
if __name__ == "__main__":
    print("Bot starting...")
    
    # This loop will keep your bot running indefinitely on Render.com
    while True:
        # Run the async function using asyncio
        asyncio.run(fetch_and_post_headlines()) 
        
        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) 
        # Using time.sleep() outside of the async function is fine for the main loop
        print(f"[{current_time_str}] Sleeping for 1 minute...") # Adjusted sleep message
        time.sleep(60) # Changed to 1 minute as requested
