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
TELEGRAM_CHANNEL_ID          = os.getenv("TELEGRAM_CHANNEL_ID")          # HMM News
TELEGRAM_CRYPTO_CHANNEL_ID   = os.getenv("TELEGRAM_CRYPTO_CHANNEL_ID")   # Crypto News
TELEGRAM_CRYPTO_TOKEN        = os.getenv("TELEGRAM_CRYPTO_TOKEN")        # Crypto Bot Token

RSS_URLS_RAW        = os.getenv("RTT_RSS_FEED_URL", "")
CRYPTO_RSS_FEEDS_RAW = os.getenv("CRYPTO_RSS_FEEDS", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing required environment variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]
CRYPTO_RSS_FEEDS = [u.strip() for u in CRYPTO_RSS_FEEDS_RAW.split(",") if u.strip()]

###############################################################################
# 3. Database State Management (Replaces Text Files)
###############################################################################
# We store the bot's memory in Firestore so it survives Render restarts

def get_bot_state(doc_name='main_config'):
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

def save_bot_state(last_link, last_time, doc_name='main_config'):
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
# 4. Glossary & Translation
###############################################################################
# Included inline to prevent "ModuleNotFound" errors on Render
GLOSSARY = {
    "futures": "qandaraasyada mustaqbalka",
    "yields": "wax-soo-saarka bonds-ka",
    "bond": "bond",
    "rate cut": "hoos u dhigidda heerka dulsaarka",
    "rate hike": "kor u qaadista heerka dulsaarka",
    "inflation": "sicirka maciishadda",
    "CPI": "CPI",
    "core inflation": "sicir-bararka asaasiga ah",
    "central bank": "bangiga dhexe",
    "Federal Reserve": "Bangiga Dhexe ee Maraykanka",
    "RBA": "Bangiga Dhexe ee Australiya",
    "BOE": "Bangiga Ingiriiska",
    "BOJ": "Bangiga Japan",
    "ECB": "Bangiga Yurub",
    "GDP": "wax-soo-saarka guud ee dalka",
    "recession": "hoos u dhac dhaqaale",
    "unemployment": "shaqo la'aan",
    "employment": "shaqaalaysiinta"
}

def apply_glossary(text: str) -> str:
    for eng, som in GLOSSARY.items():
        pattern = re.compile(r"\b" + re.escape(eng) + r"\b", re.IGNORECASE)
        text = pattern.sub(som, text)
    return text

async def translate_to_somali(text: str) -> str:
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

            # Two-step translation for higher quality (as per your original code)
            step1 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Translate this financial or crypto news into Somali clearly and accurately."},
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
                            "You are a conservative financial-markets analyst. "
                            "For the given headline, determine:\n"
                            "1) Tone: Bullish, Bearish, or Neutral.\n"
                            "2) Horizon: Intraday (1-4h), Short-term (1-3 days), Medium-term (1 week), or Macro (1 month+).\n"
                            "3) Confidence: integer 0-100.\n"
                            "Output strictly: 'Tone: X; Horizon: Y; Confidence: Z'"
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
# 6. Filters & Facebook
###############################################################################
TARGET_FOREX_NEWS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "JPY": "🇯🇵", "GBP": "🇬🇧",
    "CAD": "🇨🇦", "CHF": "🇨🇭", "AUD": "🇦🇺", "NZD": "🇳🇿"
}

IMPORTANT_KEYWORDS = [
    "Trump", "Biden", "White House", "Election", "Republican", "Democrat",
    "Powell", "Fed", "Federal Reserve", "FOMC",
    "Yellen", "Treasury Secretary", "ECB", "Lagarde", 
    "BOJ", "RBA", "BOE", "China PBOC", "Xi Jinping", "GDP", "CPI"
]

EXCLUSION_KEYWORDS = ["auction", "bid-to-cover", "Energy", "Coal", "NATO"]

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
            await client.post(fb_url, data=data)
    except Exception as e:
        logging.error(f"Facebook error: {e}")

###############################################################################
# 7. Main HMM: Fetch & Post Forex/Macro Headlines
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
    # LOAD STATE FROM DB (Not File)
    state = get_bot_state(doc_name='forex_state')
    last_link = state.get('last_link')
    last_time = state.get('last_time', 0.0)

    new_items = []
    for url in RSS_URLS:
        feed = feedparser.parse(url)
        for e in feed.entries:
            link = e.get("link")
            pub = e.get("published_parsed")
            if link == last_link: break
            if pub and time.mktime(pub) <= last_time: continue
            new_items.append(e)

    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())

    if not new_items:
        logging.info("📭 No new forex items.")
        return

    latest_timestamp = last_time
    latest_link = last_link

    for e in new_items:
        raw = e.title or ""
        if should_exclude_headline(raw): continue

        # Identify Flag
        flag = None
        for c, f in TARGET_FOREX_NEWS.items():
            if re.search(r"\b" + re.escape(c) + r"\b", raw, re.IGNORECASE):
                flag = f
                break
        
        if not flag:
            if any(k.lower() in raw.lower() for k in IMPORTANT_KEYWORDS):
                flag = "🇺🇸" # Default to US/Global for macro
            else:
                continue

        title = clean_title(raw)
        somali = await translate_to_somali(title)
        if not somali: continue

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

###############################################################################
# 8. Crypto: Fetch & Post
###############################################################################
CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
    "solana", "xrp", "bnb", "doge", "cardano", "ada", "polkadot"
]

async def fetch_and_post_crypto(bot: Bot):
    if not TELEGRAM_CRYPTO_CHANNEL_ID or not CRYPTO_RSS_FEEDS: return

    # LOAD STATE FROM DB (Crypto has its own memory)
    state = get_bot_state(doc_name='crypto_state')
    last_link = state.get('last_link')
    last_time = state.get('last_time', 0.0)

    new_items = []
    for url in CRYPTO_RSS_FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries[:15]:
            link = e.get("link")
            pub = e.get("published_parsed")
            if link == last_link: break
            if pub and time.mktime(pub) <= last_time: continue
            new_items.append(e)
    
    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())

    if not new_items: return

    latest_timestamp = last_time
    latest_link = last_link

    for e in new_items:
        title = e.title or ""
        if not any(k in title.lower() for k in CRYPTO_KEYWORDS): continue

        somali = await translate_to_somali(title)
        if not somali: continue

        tone, horizon, conf = await analyze_sentiment(title)

        # 1. SAVE TO DASHBOARD (As Crypto)
        save_news_to_dashboard(title, somali, tone, horizon, conf, 'Crypto', '🪙')

        # 2. SEND TO CRYPTO CHANNEL
        message = f"🪙 {somali}\n\n({tone} — {horizon} — Confidence: {conf}%)"
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CRYPTO_CHANNEL_ID,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            logging.info(f"✅ Crypto posted: {title[:40]}...")
        except Exception as err:
            logging.error(f"Crypto error: {err}")

        # Track state
        if e.get("link"): latest_link = e.get("link")
        if e.get("published_parsed"): 
            latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))

        await asyncio.sleep(2)

    # SAVE STATE TO DB
    if latest_timestamp > last_time:
        save_bot_state(latest_link, latest_timestamp, doc_name='crypto_state')

###############################################################################
# 9. Main Runner
###############################################################################
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Initialize Crypto Bot only if token exists
    crypto_bot = None
    if TELEGRAM_CRYPTO_TOKEN:
        crypto_bot = Bot(token=TELEGRAM_CRYPTO_TOKEN)
    
    logging.info("🚀 Render Bot Started. Listening for news...")

    while True:
        try:
            await fetch_and_post_headlines(bot)
            if crypto_bot:
                await fetch_and_post_crypto(crypto_bot)
        except Exception as e:
            logging.error(f"❌ Fatal error in main loop: {e}")
        
        logging.info("⏳ Sleeping 60 seconds...")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
