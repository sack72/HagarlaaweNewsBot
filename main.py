import time
import os
import pytz
import requests
import asyncio
from telegram import Bot
from googletrans import Translator
from datetime import datetime, timezone

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@HagarlaaweMarkets")

# --- Twelve Data API Configuration ---
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "YOUR_TWELVE_DATA_API_KEY")
# Using the Economic Calendar endpoint for 'news' based on economic events
TWELVE_DATA_API_URL = "https://api.twelvedata.com/economic_calendar"

# You can specify countries, e.g., "US,EU,JP,GB,CN,CA,AU" or leave it empty for all
# Note: Free tier might have limitations on country filters or historical data.
TWELVE_DATA_COUNTRIES = os.getenv("TWELVE_DATA_COUNTRIES", "US,EU,GB,JP,CN") # Example: Major economies
# Fetch events occurring in the last X minutes to catch recent releases
FETCH_WINDOW_MINUTES = int(os.getenv("FETCH_WINDOW_MINUTES", "120")) # Fetch events from last 2 hours

# --- Global Variables ---
translator = Translator()
# Using a timestamp to track the last posted event, as Twelve Data events don't have unique 'links'
# Initialize to a very old timestamp to ensure first run fetches recent events
last_posted_event_timestamp = datetime.now(timezone.utc).timestamp() - (FETCH_WINDOW_MINUTES * 60 * 2) # Go back a bit more than window

# --- Keyword Filtering ---
KEYWORDS = [
    # Macroeconomics Data (adjusted for typical economic calendar events)
    "cpi", "inflation", "gdp", "jobs report", "non-farm payrolls", "nfp",
    "interest rate", "fed", "central bank", "ecb", "boe", "boj", "fomc",
    "rate hike", "recession", "unemployment", "pmi", "trade balance",
    "retail sales", "consumer confidence", "economic outlook", "fiscal policy",
    "monetary policy", "yields", "balance of payments", "current account",
    "industrial production", "manufacturing", "services", "housing", "durable goods",
    "budget", "debt", "auctions", "reserve", "loan", "lending",

    # Institutions / Central Banks (by name, adjusted for global economic focus)
    "jpmorgan", "goldman sachs", "bank of america", "citi", "wells fargo",
    "hsbc", "barclays", "deutsche bank", "ubs", "federal reserve",
    "european central bank", "bank of england", "bank of japan", "imf",
    "world bank", "moody's", "s&p", "fitch", "bank of international settlements",
    "opec", "g7", "g20", "un", "world trade organization", "wto", "oecd",

    # Major Currencies (relevant for economic events)
    "usd", "eur", "jpy", "gbp", "chf", "cad", "aud", "nzd", "yen", "pound",
    "euro", "dollar", "currency", "forex", "fx", "greenback",
    "yuan", "cny", "zar", "mxn", "brl", "try", "rub", "inr"
]

# --- Somali prefixes to remove from translation (adjust as needed for Twelve Data translations) ---
SOMALI_PREFIXES_TO_REMOVE = [
    "Qaybta:", # Common googletrans artifact
    "Fielding:", # Common googletrans artifact
    "Dhaqaalaha:", # Common googletrans artifact
    # You may need to add or remove more prefixes based on actual translations from Twelve Data events
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
    Fetches new economic calendar events from Twelve Data API, filters them by keywords,
    translates them to Somali, and posts them to the Telegram channel.
    """
    global last_posted_event_timestamp

    current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    print(f"[{current_time_str}] Checking Twelve Data Economic Calendar API...")
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # Calculate time range for events
    end_time = datetime.now(timezone.utc)
    start_time = end_time - pytz.utc.localize(datetime.fromtimestamp(FETCH_WINDOW_MINUTES * 60)) # Using pytz.utc.localize for consistent timezone-aware datetime

    params = {
        "apikey": TWELVE_DATA_API_KEY,
        "symbol": TWELVE_DATA_COUNTRIES, # Use 'symbol' for countries in economic_calendar
        "interval": "1day", # Fetch events within the specified time window, not fixed interval
        "outputsize": 100 # Max number of events to fetch, adjust if needed
    }

    try:
        response = requests.get(TWELVE_DATA_API_URL, params=params)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()

        if "code" in data and data["code"] != 200:
            print(f"Twelve Data API Error: {data.get('message', 'Unknown error')}")
            return
        
        if not data or "data" not in data or not isinstance(data["data"], list):
            print("No valid data received from Twelve Data API or unexpected format.")
            return

        all_events = data["data"]
        # Sort events by timestamp to process oldest first and track latest properly
        all_events.sort(key=lambda x: datetime.strptime(f"{x['date']} {x['time']}", "%Y-%m-%d %H:%M").timestamp())
        
        new_events_to_process = []
        for event in all_events:
            try:
                # Combine date and time to create a datetime object for comparison
                event_datetime_str = f"{event['date']} {event['time']}"
                event_datetime = datetime.strptime(event_datetime_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                event_timestamp = event_datetime.timestamp()

                # Only process events newer than the last posted event
                if event_timestamp > last_posted_event_timestamp:
                    new_events_to_process.append(event)
            except ValueError as ve:
                print(f"Skipping event due to date/time parsing error: {event_datetime_str} - {ve}")
                continue # Skip to next event

        if not new_events_to_process:
            print("No new economic events to post.")
            return

        print(f"Found {len(new_events_to_process)} new economic events. Applying filters...")

        filtered_headlines_count = 0
        current_session_latest_timestamp = last_posted_event_timestamp

        for event in new_events_to_process:
            english_headline = f"({event.get('country', 'N/A')}) {event.get('event_name', 'No event name')}"
            
            # --- Keyword Filtering Logic ---
            if not contains_keywords(english_headline, KEYWORDS):
                print(f"Skipping (no keywords): '{english_headline}'")
                continue

            print(f"Processing (contains keywords): '{english_headline}'")
            filtered_headlines_count += 1

            try:
                await asyncio.sleep(0.5) # Be mindful of Twelve Data API rate limits

                # Translate the headline
                translated_text_obj = await translator.translate(english_headline, dest='so')
                somali_headline = translated_text_obj.text

                # --- Clean known Somali prefixes from translation ---
                for prefix in SOMALI_PREFIXES_TO_REMOVE:
                    if somali_headline.startswith(prefix):
                        somali_headline = somali_headline[len(prefix):].strip()
                somali_headline = somali_headline.strip() # Final strip

                # Add event details to the message
                event_time_utc = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                
                message_to_send = (
                    f"**DEGDEG 🔴 Wararka Dhaqaalaha**\n\n"
                    f"**Goobta:** {event.get('country', 'N/A')} - **Waqtiga (UTC):** {event_time_utc.strftime('%H:%M %b %d, %Y')}\n\n"
                    f"🇬🇧: {english_headline}\n"
                    f"🇸🇴: {somali_headline}\n\n"
                    f"**Saadaasha:** {event.get('forecast', 'N/A')}\n"
                    f"**Tiirkii Hore:** {event.get('previous', 'N/A')}"
                )
                if event.get('actual') is not None: # Actual data might not be available immediately
                    message_to_send += f"\n**Xaqiiqda:** {event['actual']}"

                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=message_to_send,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                print(f"Posted Somali: '{somali_headline}' (Original: '{english_headline}')")

                # Update the latest timestamp seen in this session
                current_session_latest_timestamp = max(current_session_latest_timestamp, event_datetime.timestamp())

            except Exception as e:
                print(f"Error translating or posting event '{english_headline}': {e}")
                try:
                    # Fallback Message
                    fallback_message = (
                        f"**DEGDEG 🔴 Wararka Dhaqaalaha**\n\n"
                        f"**Goobta:** {event.get('country', 'N/A')} - **Waqtiga (UTC):** {event_time_utc.strftime('%H:%M %b %d, %Y')}\n\n"
                        f"🇬🇧: {english_headline}\n\n"
                        f"Fadlan dib u eeg sababtoo ah khalad ayaa ku yimid tarjumidda."
                    )
                    await bot.send_message(
                        chat_id=TELEGRAM_CHANNEL_ID,
                        text=fallback_message,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    print(f"Posted original English due to translation error: '{english_headline}'")
                except Exception as inner_e:
                    print(f"Failed to post even original English event '{english_headline}': {inner_e}")
        
        # After processing all new events, update the global last_posted_event_timestamp
        # to prevent reprocessing them on the next run.
        last_posted_event_timestamp = current_session_latest_timestamp
        print(f"Updated last_posted_event_timestamp to: {datetime.fromtimestamp(last_posted_event_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    except requests.exceptions.RequestException as e:
        print(f"Network or API request error: {e}")
    except ValueError as e: # For JSON decoding errors
        print(f"JSON decoding error from API response: {e}")
    except Exception as e:
        print(f"An unexpected error occurred in fetch_and_post_headlines: {e}")

    if filtered_headlines_count == 0 and len(new_events_to_process) > 0:
        print("No new economic events matched the keyword filter.")


# --- Main Execution Loop ---
if __name__ == "__main__":
    print("Bot starting...")
    print(f"Configured to fetch from Twelve Data API for countries: {TWELVE_DATA_COUNTRIES}")
    print(f"Fetching events from the last {FETCH_WINDOW_MINUTES} minutes.")

    while True:
        # Ensure the asyncio loop is managed properly
        asyncio.run(fetch_and_post_headlines())

        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        print(f"[{current_time_str}] Sleeping for 1 minute (adjust based on API limits and needs)...")
        time.sleep(60) # Sleep for 1 minute before checking again

