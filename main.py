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
try:
    from glossary import GLOSSARY
except ImportError:
    logging.error("❌ glossary.py not found! Make sure it is in your GitHub repo.")
    sys.exit(1)

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

###############################################################################
# 1. Initialize Firebase
###############################################################################
try:
    if not firebase_admin._apps:
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
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID  = os.getenv("TELEGRAM_CHANNEL_ID")
RSS_URLS_RAW         = os.getenv("RTT_RSS_FEED_URL", "")
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing required environment variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]

###############################################################################
# 3. Database State Management
###############################################################################
def get_bot_state(doc_name='forex_state'):
    try:
        doc = db.collection('bot_state').document(doc_name).get()
        return doc.to_dict() if doc.exists else {"last_link": None, "last_time": 0.0}
    except Exception as e:
        logging.error(f"⚠️ Error reading state: {e}")
        return {"last_link": None, "last_time": 0.0}

def save_bot_state(last_link, last_time, doc_name='forex_state'):
    try:
        db.collection('bot_state').document(doc_name).set({
            "last_link": last_link,
            "last_time": last_time
        }, merge=True)
    except Exception as e:
        logging.error(f"⚠️ Error saving state: {e}")

def save_news_to_dashboard(title, somali_text, analysis, flag):
    try:
        db.collection('market_sentiment').add({
            'title': title,
            'somali_text': somali_text,
            'sentiment': analysis['sentiment'],
            'asset': analysis['asset'],
            'reason': analysis['reason'],
            'impact': analysis['impact'],
            'flag': flag,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        logging.info(f"📊 Dashboard data saved.")
    except Exception as e:
        logging.error(f"❌ Dashboard save error: {e}")

###############################################################################
# 4. Translation Logic
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

            system_instruction = (
                "You are a professional Somali financial news translator. "
                "CONTEXT: The current date is 2026. "
                "Donald Trump is the CURRENT President of the USA. "
                "Do NOT refer to him as 'former' or 'hore'. "
                "Translate accurately reflecting this status."
            )

            step1 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Translate this headline to Somali: {text}"}
                ],
                temperature=0.2,
                max_tokens=300,
            )
            first_pass = step1.choices[0].message.content.strip()

            step2 = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Refine this into professional, concise Somali financial news style."},
                    {"role": "user", "content": first_pass}
                ],
                temperature=0.3,
                max_tokens=300,
            )
            result = apply_glossary(step2.choices[0].message.content.strip())
            
            # Safety Fixes
            result = re.sub(r"Madaxweynihii hore ee", "Madaxweynaha", result, flags=re.IGNORECASE)
            result = re.sub(r"Madaxwaynihii hore ee", "Madaxweynaha", result, flags=re.IGNORECASE)
            
            return result
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        return ""

###############################################################################
# 5. ADVANCED AI ANALYST (Structured Output)
###############################################################################
async def analyze_sentiment_advanced(text: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            
            system_prompt = (
                "You are an expert Forex Market Analyst. Analyze the news headline. "
                "Output strictly in this format (separate with pipes |): "
                "Sentiment: [Bullish/Bearish/Neutral] | "
                "Asset: [The main currency pair or asset affected, e.g., USD, EUR, Gold] | "
                "Reason: [One very short sentence in Somali explaining why, e.g., 'Sicir-bararka oo kordhay ayaa taageeraya Doolarka.'] | "
                "Impact: [High/Medium/Low]"
            )
            
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=150,
            )
            out = resp.choices[0].message.content.strip()
            
            # Default values
            data = {"sentiment": "Neutral", "asset": "Market", "reason": "Warbixin caadi ah.", "impact": "Low"}
            
            # Parse the Pipe | Separated Output
            parts = out.split("|")
            for p in parts:
                if "Sentiment:" in p: data["sentiment"] = p.replace("Sentiment:", "").strip()
                if "Asset:" in p: data["asset"] = p.replace("Asset:", "").strip()
                if "Reason:" in p: data["reason"] = p.replace("Reason:", "").strip()
                if "Impact:" in p: data["impact"] = p.replace("Impact:", "").strip()
                
            return data

    except Exception as e:
        logging.error(f"Analysis failed: {e}")
        return {"sentiment": "Neutral", "asset": "N/A", "reason": "", "impact": "Low"}

###############################################################################
# 6. FILTERS (Currently Disabled for Testing)
###############################################################################
TARGET_FOREX_NEWS = {
    "USD": "🇺🇸", "United States": "🇺🇸", "US": "🇺🇸", "Fed": "🇺🇸", "FOMC": "🇺🇸", "Powell": "🇺🇸", "Trump": "🇺🇸",
    "EUR": "🇪🇺", "Europe": "🇪🇺", "Eurozone": "🇪🇺", "ECB": "🇪🇺", "Lagarde": "🇪🇺",
    "JPY": "🇯🇵", "Japan": "🇯🇵", "BOJ": "🇯🇵", "Ueda": "🇯🇵", "Yen": "🇯🇵",
    "GBP": "🇬🇧", "UK": "🇬🇧", "Britain": "🇬🇧", "BOE": "🇬🇧", "Bailey": "🇬🇧",
    "CAD": "🇨🇦", "Canada": "🇨🇦", "BOC": "🇨🇦",
    "AUD": "🇦🇺", "Australia": "🇦🇺", "RBA": "🇦🇺",
    "NZD": "🇳🇿", "New Zealand": "🇳🇿", "RBNZ": "🇳🇿",
    "CHF": "🇨🇭", "Swiss": "🇨🇭", "SNB": "🇨🇭"
}

HIGH_IMPACT_KEYWORDS = [
    "CPI", "PPI", "PCE", "Inflation", "NFP", "Payrolls", "Unemployment", 
    "Interest Rate", "Rate Decision", "GDP", "Retail Sales", "PMI", 
    "Election", "War", "Geopolitical", "President"
]

EXCLUSION_KEYWORDS = ["auction", "bid-to-cover", "close", "open", "preview", "review", "summary"]

def is_major_currency(text: str) -> str:
    for keyword, flag in TARGET_FOREX_NEWS.items():
        if re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE):
            return flag
    return "🌍" # Default flag for testing

def is_high_impact(text: str) -> bool:
    for keyword in HIGH_IMPACT_KEYWORDS:
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
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"https://graph.facebook.com/{page_id}/feed", data={"message": message, "access_token": page_token})
            logging.info("✅ Posted to Facebook.")
    except Exception as e:
        logging.error(f"Facebook error: {e}")

###############################################################################
# 7. Main Loop
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
    state = get_bot_state()
    last_link = state.get('last_link')
    last_time = state.get('last_time', 0.0)

    new_items = []
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                if e.get("link") == last_link: break
                pub = e.get("published_parsed")
                if pub and time.mktime(pub) <= last_time: continue
                new_items.append(e)
        except Exception as e:
            logging.error(f"Feed error {url}: {e}")

    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())
    if not new_items: return

    latest_timestamp = last_time
    latest_link = last_link

    for e in new_items:
        raw = e.title or ""
        
        # 1. Exclude Junk
        if should_exclude_headline(raw): continue

        # --- 🚨 TEST MODE: FILTERS DISABLED 🚨 ---
        # To enable filters later, uncomment these lines:
        # flag = is_major_currency(raw)
        # if not flag: continue
        # if not is_high_impact(raw): continue
        
        flag = is_major_currency(raw) 
        # -----------------------------------------

        logging.info(f"📰 Processing: {raw}")
        title = clean_title(raw)
        
        # Translate
        somali = await translate_to_somali(title)
        if not somali: continue

        # Advanced Analysis
        analysis = await analyze_sentiment_advanced(title)

        # Build Emojis
        sent_emoji = "⚖️"
        if "Bullish" in analysis['sentiment']: sent_emoji = "📈"
        elif "Bearish" in analysis['sentiment']: sent_emoji = "📉"
        
        impact_emoji = "🟢"
        if "High" in analysis['impact']: impact_emoji = "🔴"
        elif "Medium" in analysis['impact']: impact_emoji = "🟠"

        # --- SAVE TO DASHBOARD ---
        save_news_to_dashboard(title, somali, analysis, flag)

        # --- CONSTRUCT PRO MESSAGE (NO ENGLISH TITLE) ---
        message = (
            f"{flag} **{somali}**\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 **Falanqeynta Suuqa:**\n"
            f"🔹 **Saameynta:** {analysis['asset']} {sent_emoji} ({analysis['sentiment']})\n"
            f"🔹 **Sababta:** {analysis['reason']}\n"
            f"🔹 **Muhiimadda:** {analysis['impact']} {impact_emoji}"
        )

        try:
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message, parse_mode="Markdown", disable_web_page_preview=True)
            logging.info(f"✅ Telegram Posted.")
            
            # Facebook
            fb_message = message.replace("**", "").replace("🔹", "-") + "\n\n#HagarlaaweHMM #ForexSomali"
            await post_to_facebook(fb_message)
            
        except Exception as err:
            logging.error(f"Telegram error: {err}")

        if e.get("link"): latest_link = e.get("link")
        if e.get("published_parsed"): latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))
        
        await asyncio.sleep(1)

    if latest_timestamp > last_time:
        save_bot_state(latest_link, latest_timestamp)

async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logging.info("🚀 Bot Started (TEST MODE: ALL NEWS). Filters Disabled.")
    while True:
        try:
            await fetch_and_post_headlines(bot)
        except Exception as e:
            logging.error(f"❌ Main loop error: {e}")
        logging.info("⏳ Waiting 60s...")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
