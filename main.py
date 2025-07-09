import feedparser
from telegram import Bot
from googletrans import Translator # Ensure googletrans is installed via requirements.txt
import time
import os
import pytz # Import pytz for timezone awareness if needed (from your requirements)
import requests # From your requirements, though not directly used in this feedparser/googletrans flow

# --- Configuration ---
# It's best practice to get sensitive information from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@HagarlaaweMarkets") # Your channel username (e.g., @MyBotChannel)
FINANCIAL_JUICE_RSS_FEED_URL = os.getenv("FINANCIAL_JUICE_RSS_FEED_URL", "YOUR_FINANCIAL_JUICE_RSS_FEED_URL") 

# --- Global Variables ---
translator = Translator()
# A simple in-memory store for last posted item.
# For persistence across bot restarts, consider a file or a simple database.
last_posted_link = None 

# --- Functions ---

def fetch_and_post_headlines():
    """
    Fetches new headlines from the RSS feed, translates them to Somali,
    and posts them to the Telegram channel.
    """
    global last_posted_link # Declare global to modify the variable

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Checking RSS feed from: {FINANCIAL_JUICE_RSS_FEED_URL}")
    feed = feedparser.parse(FINANCIAL_JUICE_RSS_FEED_URL)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # To avoid re-posting after restarts, you'd typically load `last_posted_link`
    # from a file or environment variable at bot startup.
    # For this example, it only prevents duplicates within a single continuous run.

    new_entries_to_process = []
    
    # Iterate through entries from newest to oldest to find truly new ones efficiently
    # and then process them in chronological order.
    # If `last_posted_link` is None (first run or restart), it will process all.
    for entry in feed.entries:
        if entry.link == last_posted_link:
            # We've reached the last headline we already posted, stop here
            break
        new_entries_to_process.append(entry)
    
    # Reverse to process from oldest new headline to newest new headline
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
            time.sleep(0.5) 
            translated_text_obj = translator.translate(english_headline, dest='so')
            somali_headline = translated_text_obj.text
            
            # --- Message Format ---
            # You can choose to send only Somali or both.
            # This example sends both, formatted nicely with Markdown.
            message_to_send = (
                f"**HAGARLAAWE MARKETS NEWS**\n\n"
                f"🇬🇧: {english_headline}\n\n"
                f"🇸🇴: {somali_headline}\n\n"
                f"[Read More]({post_url})" 
            )
            
            bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode='Markdown', # Allows for bolding and clickable links
                disable_web_page_preview=True # Prevents Telegram from auto-generating link previews
            )
            print(f"Posted: '{english_headline}' -> '{somali_headline}'")
            
            # Update the last posted link after successful posting
            last_posted_link = entry.link 
            
            # Add a small delay between Telegram messages to avoid API rate limits
            time.sleep(1) 

        except Exception as e:
            print(f"Error translating or posting headline '{english_headline}': {e}")
            # Fallback: if translation fails, post the original English headline
            try:
                fallback_message = (
                    f"**HAGARLAAWE MARKETS NEWS (Translation Failed)**\n\n"
                    f"Original English:\n{english_headline}\n\n"
                    f"[Read More]({post_url})"
                )
                bot.send_message(
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
    
    # You might want to initialize last_posted_link from a persistent store here
    # For example:
    # try:
    #     with open('last_link.txt', 'r') as f:
    #         last_posted_link = f.read().strip()
    #         print(f"Loaded last posted link: {last_posted_link}")
    # except FileNotFoundError:
    #     print("No last_link.txt found. Starting fresh.")
    
    # This loop will keep your bot running on Render.com
    while True:
        fetch_and_post_headlines()
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sleeping for 15 minutes...")
        time.sleep(15 * 60) # Check every 15 minutes (adjust based on feed update frequency and desired latency)

