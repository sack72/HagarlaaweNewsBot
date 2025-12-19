import os
import time
import re
import asyncio
import logging
import feedparser
import sys
import httpx
from telegram import Bot
from openai import AsyncOpenAI
from typing import Optional, List, Dict, Any

# --- FIREBASE SETUP ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- IMPORT YOUR GLOSSARY ---
# Make sure glossary.py is in the same folder in your GitHub repo!
try:
    from glossary import GLOSSARY
except ImportError:
    logging.error("❌ glossary.py not found! Make sure it is in your GitHub repo.")
    sys.exit(1)

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

###############################################################################
# 1. Initialize Firebase (The Brain & Memory)
###############################################################################
try:
    if not firebase_admin._apps:
        # On Render, this file must be in "Secret Files"
        if os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")
        else:
            logging.error("❌ serviceAccountKey.json not found! Upload it to Render 'Secret Files'.")
            sys.exit(1)
            
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    logging.info("✅ Connected to Firebase Firestore")
except Exception as e:
    logging.error(f"❌ Failed to initialize Firebase: {e}")
    sys.exit(1)

###############################################################################
# 2. Environment & Setup
###############################################################################
TELEGRAM_BOT_TOKEN           = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID          = os.getenv("TELEGRAM_CHANNEL_ID")
RSS_URLS_RAW                 = os.getenv("RTT_RSS_FEED_URL", "")
OPENAI_API_KEY               = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing required environment variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]

###############################################################################
# 3. Database State Management
###############################################################################
def get_bot_state(doc_name='forex_state'):
    """Retrieves last_link and last_time from Firestore"""
    try:
        doc = db.collection('bot_state').document(doc_name).get()
        if doc.exists:
            return doc.to_dict()
        else:
            return {"last_link": None, "last_time": 0.0}
    except Exception as e:
        logging.error(f"⚠️ Error reading state from DB: {e}")
        return {"last_link": None, "last_time": 0.0}

def save_bot_state(last_link, last_time, doc_name='forex_state'):
    """Saves current state to Firestore"""
    try:
        db.collection('bot_state').document(doc_name).set({
            "last_link": last_link,
            "last_time": last_time
        }, merge=True)
    except Exception as e:
        logging.error(f"⚠️ Error saving state to DB: {e}")

def save_news_to_dashboard(title, somali_text, tone, horizon, confidence, asset_type, flag):
    """Saves the news item to Firestore for the Website Dashboard"""
    try:
        db.collection('market_sentiment').add({
            'title': title,
            'somali_text': somali_text,
            'sentiment': tone,
            'horizon': horizon,
            'confidence': confidence,
            'type': asset_type,
            'flag': flag,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        logging.info(f"📊 Dashboard data saved: {title[:20]}...")
    except Exception as e:
        logging.error(f"❌ Dashboard save error: {e}")

###############################################################################
# 4. Translation with External Glossary
###############################################################################
def apply_glossary(text: str) -> str:
    for eng, som in GLOSSARY.items():
        pattern = re.compile(r"\b" + re.escape(eng) + r"\b", re.IGNORECASE)
        text = pattern.sub(som, text)
    return text

async def translate_to_somali(text: str) -> str:
    try:
        logging.info(f"📝 Translating: {text}")
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

            step1 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Translate this financial news into Somali clearly and accurately."},
                    {"role": "user", "content": text}
                ],
                temperature=0.2,
                max_tokens=300,
            )
            first_pass = step1.choices[0].message.content.strip()

            step2 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Rewrite in professional Somali financial-news style, concise and clear."},
                    {"role": "user", "content": first_pass}
                ],
                temperature=0.3,
                max_tokens=300,
            )
            result = apply_glossary(step2.choices[0].message.content.strip())
            return result
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        return ""

###############################################################################
# 5. Sentiment Analysis
###############################################################################
async def analyze_sentiment(text: str) -> tuple[str, str, int]:
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Determine: 1) Tone (Bullish/Bearish/Neutral) 2) Horizon (Intraday/Short-term/Medium-term) 3) Confidence (0-100). "
                            "Format strictly: 'Tone: X; Horizon: Y; Confidence: Z'"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=50,
            )
            out = resp.choices[0].message.content.strip()

        tone_match = re.search(r"(Bullish|Bearish|Neutral)", out, re.IGNORECASE)
        horizon_match = re.search(r"(Intraday|Short-term|Medium-term|Macro)", out, re.IGNORECASE)
        conf_match = re.search(r"Confidence:\s*(\d{1,3})", out)

        tone = tone_match.group(1).capitalize() if tone_match else "Neutral"
        horizon = horizon_match.group(1).capitalize() if horizon_match else "Unknown"
        try:
            conf = int(conf_match.group(1)) if conf_match else 50
        except:
            conf = 50
        conf = max(0, min(100, conf))
        return (tone, horizon, conf)
    except Exception as e:
        logging.error(f"Sentiment analysis failed: {e}")
        return ("Neutral", "Unknown", 50)

###############################################################################
# 6. FILTERS: HIGH IMPACT ONLY
###############################################################################

# Used for visual flags only
TARGET_FOREX_NEWS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "JPY": "🇯🇵", "GBP": "🇬🇧",
    "CAD": "🇨🇦", "CHF": "🇨🇭", "AUD": "🇦🇺", "NZD": "🇳🇿",
    "United States": "🇺🇸", "US": "🇺🇸",
    "Europe": "🇪🇺", "Japan": "🇯🇵", "UK": "🇬🇧", "Britain": "🇬🇧",
    "Canada": "🇨🇦", "Swiss": "🇨🇭", "Australia": "🇦🇺", "New Zealand": "🇳🇿"
}

# 🚨 STRICT FILTER: Headline MUST contain one of these to be posted
HIGH_IMPACT_KEYWORDS = [
    # --- Inflation Data ---
    "CPI", "PPI", "PCE", "Inflation", "Consumer Price Index",
    
    # --- Employment/Jobs ---
    "Non-Farm", "NFP", "Payrolls", "Unemployment Rate", "Jobless Claims", 
    "Employment Change", "Average Earnings",
    
    # --- Central Banks (Decisions Only) ---
    "Interest Rate", "Rate Decision", "Rate Hike", "Rate Cut", 
    "Monetary Policy", "FOMC", "Fed Chair", "Powell", 
    "Lagarde", "Bailey", "Ueda", "Macklem", "Orr", "Lowe", "Bullock",
    "Meeting Minutes", "ECB", "BOE", "BOJ", "RBA", "RBNZ", "BOC",
    
    # --- Growth & Activity ---
    "GDP", "Gross Domestic Product", "Retail Sales", "PMI", "ISM", 
    "Consumer Confidence", "Sentiment", "Durable Goods",
    
    # --- Major Geopolitics ---
    "Election", "War", "Geopolitical"
]

EXCLUSION_KEYWORDS = [
    "auction", "bid-to-cover", "Energy", "Coal", "NATO", 
    "ETF", "Stocks", "close", "open", "futures", "preview",
    "review", "wrap", "morning", "evening"
]

def is_high_impact(text: str) -> bool:
    """Checks if the text contains a high-impact keyword."""
    for keyword in HIGH_IMPACT_KEYWORDS:
        # \b ensures we match "Fed" but not "FedEx"
        if re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE):
            return True
    return False

def should_exclude_headline(title: str) -> bool:
    for k in EXCLUSION_KEYWORDS:
        if k.lower() in title.lower(): return True
    return False

def clean_title(t: str) -> str:
    t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}:?\s*", "", t)
    t = re.sub(r"^[^:]+:\s*", "", t).strip()
    return t

async def post_to_facebook(message: str) -> None:
    page_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("FACEBOOK_PAGE_ID")
    if not page_token or not page_id: return

    hashtags = "\n\n#HagarlaaweHMM #WararkaFx #Forexsomali #Dhaqaalaha #Maaliyadda"
    fb_url = f"https://graph.facebook.com/{page_id}/feed"
    data = {"message": message + hashtags, "access_token": page_token}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(fb_url, data=data)
            if r.status_code == 200:
                logging.info("✅ Posted to Facebook successfully.")
            else:
                logging.error(f"❌ Facebook post failed: {r.text}")
    except Exception as e:
        logging.error(f"Facebook error: {e}")

###############################################################################
# 7. Main Runner Logic
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
    # LOAD STATE FROM DB
    state = get_bot_state(doc_name='forex_state')
    last_link = state.get('last_link')
    last_time = state.get('last_time', 0.0)

    new_items = []
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                link = e.get("link")
                pub = e.get("published_parsed")
                if link == last_link: break
                if pub and time.mktime(pub) <= last_time: continue
                new_items.append(e)
        except Exception as e:
            logging.error(f"Feed error {url}: {e}")

    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())

    if not new_items:
        return

    latest_timestamp = last_time
    latest_link = last_link

    for e in new_items:
        raw = e.title or ""
        
        # 1. First, check exclusions
        if should_exclude_headline(raw): continue

        # 2. HIGH IMPACT CHECK: Skip if it is not a major event
        if not is_high_impact(raw):
            # Logging this helps you verify it is filtering correctly
            # logging.info(f"⏩ Skipped (Low Impact): {raw[:30]}...") 
            continue

        # 3. Identify Flag (For visual only)
        flag = "🌍" 
        for c, f in TARGET_FOREX_NEWS.items():
            if re.search(r"\b" + re.escape(c) + r"\b", raw, re.IGNORECASE):
                flag = f
                break

        logging.info(f"📰 Processing HIGH IMPACT: {raw}")
        title = clean_title(raw)
        
        # Translate
        somali = await translate_to_somali(title)
        if not somali: continue

        # Analyze
        tone, horizon, conf = await analyze_sentiment(title)

        # 1. SAVE TO DASHBOARD
        save_news_to_dashboard(title, somali, tone, horizon, conf, 'Forex', flag)

        # 2. SEND TO TELEGRAM
        message = f"{flag} {somali}\n\n({tone} — {horizon} — Confidence: {conf}%)"
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            logging.info(f"✅ HMM Posted: {title[:40]}...")
            
            # 3. POST TO FACEBOOK
            await post_to_facebook(message)

        except Exception as err:
            logging.error(f"Telegram error: {err}")

        # Track state
        if e.get("link"): latest_link = e.get("link")
        if e.get("published_parsed"): 
            latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))
        
        await asyncio.sleep(1)

    # SAVE STATE TO DB
    if latest_timestamp > last_time:
        save_bot_state(latest_link, latest_timestamp, doc_name='forex_state')

async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logging.info("🚀 Render Forex Bot Started (HIGH IMPACT MODE). Listening for news...")

    while True:
        try:
            await fetch_and_post_headlines(bot)
        except Exception as e:
            logging.error(f"❌ Fatal error in main loop: {e}")
        
        # Check every 60 seconds
        logging.info("⏳ Sleeping 60 seconds...")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
