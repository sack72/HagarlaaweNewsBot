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

# --- IMPORT GLOSSARY ---
try:
    from glossary import GLOSSARY
except ImportError:
    logging.error("❌ glossary.py not found!")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 1. INITIALIZE FIREBASE
try:
    if not firebase_admin._apps:
        if os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")
        else:
            sys.exit(1)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    logging.info("✅ Firebase Connected")
except Exception as e:
    logging.error(f"❌ Firebase Error: {e}")
    sys.exit(1)

# 2. ENVIRONMENT VARIABLES
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID  = os.getenv("TELEGRAM_CHANNEL_ID")
RSS_URLS_RAW         = os.getenv("RTT_RSS_FEED_URL", "")
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing ENV variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]

# ------------------------------------------------------------------
# 3. IMPACT DEFINITIONS (Red vs Orange)
# ------------------------------------------------------------------

# 🔴 RED FOLDER (High Impact)
RED_FOLDER_KEYWORDS = [
    "Non-Farm", "NFP", "Unemployment Rate", "CPI", "Interest Rate", 
    "Fed Chair", "FOMC", "ECB President", "BOE Governor", "BOJ Governor", 
    "GDP", "Retail Sales", "Rate Decision", "Statement", "Monetary Policy",
    "Powell", "Lagarde", "Bailey", "Ueda", "Trump"
]

# 🟠 ORANGE FOLDER (Medium Impact)
ORANGE_FOLDER_KEYWORDS = [
    "PPI", "Producer Price", "Core PCE", "Consumer Confidence", 
    "Building Permits", "Housing Starts", "ISM", "PMI", "Trade Balance", 
    "JOLTS", "ADP", "Claimant Count", "Zew", "Ifo", "Tankan"
]

# 🟢 TARGET CURRENCIES (Top 8)
TARGET_CURRENCIES = {
    "USD": "🇺🇸", "US": "🇺🇸", "Fed": "🇺🇸", "FOMC": "🇺🇸", "Powell": "🇺🇸", "Trump": "🇺🇸",
    "EUR": "🇪🇺", "Europe": "🇪🇺", "ECB": "🇪🇺", "Lagarde": "🇪🇺",
    "JPY": "🇯🇵", "Japan": "🇯🇵", "BOJ": "🇯🇵", "Ueda": "🇯🇵",
    "GBP": "🇬🇧", "UK": "🇬🇧", "BOE": "🇬🇧", "Bailey": "🇬🇧",
    "CAD": "🇨🇦", "Canada": "🇨🇦", "BOC": "🇨🇦", "Macklem": "🇨🇦",
    "AUD": "🇦🇺", "Australia": "🇦🇺", "RBA": "🇦🇺", "Bullock": "🇦🇺",
    "NZD": "🇳🇿", "New Zealand": "🇳🇿", "RBNZ": "🇳🇿", "Orr": "🇳🇿",
    "CHF": "🇨🇭", "Swiss": "🇨🇭", "SNB": "🇨🇭", "Jordan": "🇨🇭"
}

# 🗣️ CLUSTER KEYWORDS (Triggers Buffering)
# If a news title has these, we WAIT and summarize later.
CLUSTER_KEYWORDS = [
    "Speech", "Testimony", "Press Conference", "Meeting Minutes", 
    "Statement", "Trump", "Powell", "Lagarde", "Bailey", "Ueda", "Q&A"
]

EXCLUSION_KEYWORDS = ["auction", "bid-to-cover", "close", "open", "preview", "review", "summary", "poll", "wrap"]

# ------------------------------------------------------------------
# 4. BUFFERING SYSTEM (The "Waiting Room")
# ------------------------------------------------------------------
# Structure: { 'event_key': {'headlines': [], 'start_time': timestamp} }
news_buffer = {}

BUFFER_TIMEOUT_SECONDS = 300  # Wait 5 minutes max to collect headlines
MAX_BUFFER_SIZE = 10          # Or post if we hit 10 headlines

# ------------------------------------------------------------------
# 5. HELPER FUNCTIONS
# ------------------------------------------------------------------
def get_bot_state():
    try:
        doc = db.collection('bot_state').document('forex_state').get()
        return doc.to_dict() if doc.exists else {"last_link": None, "last_time": 0.0}
    except:
        return {"last_link": None, "last_time": 0.0}

def save_bot_state(last_link, last_time):
    try:
        db.collection('bot_state').document('forex_state').set({
            "last_link": last_link, "last_time": last_time
        }, merge=True)
    except Exception as e:
        logging.error(f"DB Error: {e}")

def get_flag_and_impact(text):
    flag = None
    impact = None
    
    # Check Currency
    for k, f in TARGET_CURRENCIES.items():
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            flag = f
            break
            
    # Check Red Impact
    for k in RED_FOLDER_KEYWORDS:
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            impact = "🔴" # High
            break
            
    # Check Orange Impact (if not Red)
    if not impact:
        for k in ORANGE_FOLDER_KEYWORDS:
            if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
                impact = "🟠" # Medium
                break
                
    return flag, impact

def should_buffer(text):
    """Returns True if this is a Speech/Meeting that needs summarizing."""
    for k in CLUSTER_KEYWORDS:
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            return True
    return False

def clean_title(t):
    t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}:?\s*", "", t) # Remove existing flags
    t = re.sub(r"^[^:]+:\s*", "", t).strip()
    return t

def apply_glossary(text):
    for eng, som in GLOSSARY.items():
        pattern = re.compile(r"\b" + re.escape(eng) + r"\b", re.IGNORECASE)
        text = pattern.sub(som, text)
    return text

# ------------------------------------------------------------------
# 6. AI FUNCTIONS
# ------------------------------------------------------------------
async def summarize_cluster(headlines: List[str]) -> Dict[str, Any]:
    """
    Takes a list of 5-10 headlines and returns a SINGLE summary.
    """
    joined_text = "\n".join(headlines)
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            
            system_prompt = (
                "You are an expert Forex Analyst. "
                "I will give you a list of real-time headlines from a single event (like a Fed Speech or Trump Rally). "
                "1. Summarize the KEY takeaways into 2-3 Somali bullet points. "
                "2. Determine the overall sentiment for the asset. "
                "CONTEXT: Date is 2026. Trump is President. "
                "Output format: Sentiment: [Bullish/Bearish] | Summary: [Your Somali Summary]"
            )

            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": joined_text}
                ],
                temperature=0.2,
                max_tokens=200,
            )
            out = resp.choices[0].message.content.strip()
            
            # Parse
            sentiment = "Neutral"
            summary = out
            if "Sentiment:" in out and "|" in out:
                parts = out.split("|")
                sentiment = parts[0].replace("Sentiment:", "").strip()
                summary = parts[1].replace("Summary:", "").strip()
                
            return {"sentiment": sentiment, "summary": apply_glossary(summary)}
            
    except Exception as e:
        logging.error(f"Cluster Summary Failed: {e}")
        return {"sentiment": "Neutral", "summary": "Warbixin kooban lama heli karo."}

async def analyze_single_news(text):
    """Standard analysis for single headlines (CPI, GDP, etc)"""
    # ... (Same logic as before, just kept compact) ...
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            system_prompt = "Analyze headline. Output: Sentiment: [Bullish/Bearish] | Asset: [USD/EUR] | Reason: [Somali explanation] | Impact: [High/Med]"
            resp = await client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"system","content":system_prompt},{"role":"user","content":text}])
            out = resp.choices[0].message.content.strip()
            # Simple parsing logic
            data = {"sentiment":"Neutral", "asset":"USD", "reason":"", "impact":"Med"}
            parts = out.split("|")
            for p in parts:
                if "Sentiment:" in p: data["sentiment"] = p.replace("Sentiment:", "").strip()
                if "Asset:" in p: data["asset"] = p.replace("Asset:", "").strip()
                if "Reason:" in p: data["reason"] = p.replace("Reason:", "").strip()
                if "Impact:" in p: data["impact"] = p.replace("Impact:", "").strip()
            return data
    except:
        return {"sentiment":"Neutral", "asset":"USD", "reason":"", "impact":"Med"}

async def translate_to_somali(text):
    # Reuse your existing robust translation function
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            sys_msg = "Translate to Somali. Context: 2026, Trump is President. No 'Former'. Financial style."
            resp = await client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"system","content":sys_msg},{"role":"user","content":text}])
            res = apply_glossary(resp.choices[0].message.content.strip())
            res = re.sub(r"Madaxweynihii hore", "Madaxweynaha", res, flags=re.IGNORECASE)
            return res
    except:
        return ""

# ------------------------------------------------------------------
# 7. MAIN LOGIC
# ------------------------------------------------------------------
async def process_news_feed(bot: Bot):
    state = get_bot_state()
    last_link = state.get('last_link')
    last_time = state.get('last_time', 0.0)

    # A. FETCH
    new_items = []
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                if e.get("link") == last_link: break
                pub = e.get("published_parsed")
                if pub and time.mktime(pub) <= last_time: continue
                new_items.append(e)
        except: pass
    
    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())
    
    if new_items:
        latest_timestamp = last_time
        latest_link = last_link

        for e in new_items:
            raw = e.title or ""
            # 1. Filter Junk
            if any(k in raw.lower() for k in EXCLUSION_KEYWORDS): continue
            
            # 2. Check Impact & Currency
            flag, impact = get_flag_and_impact(raw)
            
            # STRICT FILTER: Must be Top 8 Currency AND (Red OR Orange)
            if not flag or not impact: continue

            # 3. BUFFER CHECK (Is this a Speech/Cluster event?)
            if should_buffer(raw):
                # Identify the key (e.g., "USD_SPEECH") to group them
                buffer_key = f"{flag}_SPEECH"
                current_time = time.time()
                
                if buffer_key not in news_buffer:
                    news_buffer[buffer_key] = {'headlines': [], 'start_time': current_time}
                
                news_buffer[buffer_key]['headlines'].append(clean_title(raw))
                logging.info(f"⏳ Buffered Speech Headline: {raw}")
                
                # Update trackers but DON'T POST yet
                if e.get("link"): latest_link = e.get("link")
                if e.get("published_parsed"): latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))
                continue

            # 4. STANDARD PROCESSING (Single Event like CPI)
            logging.info(f"📰 Processing Standard: {raw}")
            title = clean_title(raw)
            somali = await translate_to_somali(title)
            analysis = await analyze_single_news(title)
            
            sent_emoji = "📈" if "Bullish" in analysis['sentiment'] else "📉"
            if "Neutral" in analysis['sentiment']: sent_emoji = "⚖️"

            # Post Standard Message
            msg = (
                f"{flag} {impact} **{somali}**\n"
                f"━━━━━━━━━━━━━━\n"
                f"📊 **Falanqeynta:**\n"
                f"🔹 **Saameynta:** {analysis['asset']} {sent_emoji}\n"
                f"🔹 **Sababta:** {analysis['reason']}"
            )
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
            
            # Track
            if e.get("link"): latest_link = e.get("link")
            if e.get("published_parsed"): latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))

        # Save State
        save_bot_state(latest_link, latest_timestamp)

    # B. PROCESS BUFFERS (The Summarizer)
    # Check if any buffer is ready to be flushed
    current_time = time.time()
    keys_to_delete = []

    for key, data in news_buffer.items():
        elapsed = current_time - data['start_time']
        count = len(data['headlines'])
        
        # FLUSH CONDITION: > 5 minutes passed OR > 10 headlines collected
        if elapsed > BUFFER_TIMEOUT_SECONDS or count >= MAX_BUFFER_SIZE:
            logging.info(f"🚀 Flushing Buffer for {key} ({count} items)")
            
            # Generate Summary
            cluster_result = await summarize_cluster(data['headlines'])
            
            # Flag extraction from key
            flag_emoji = key.split("_")[0] 
            
            sent_emoji = "⚖️"
            if "Bullish" in cluster_result['sentiment']: sent_emoji = "📈"
            elif "Bearish" in cluster_result['sentiment']: sent_emoji = "📉"

            # Post Summary Message
            summary_msg = (
                f"{flag_emoji} 📣 **WARBIXIN KOOBAN (Live Update)**\n"
                f"━━━━━━━━━━━━━━\n"
                f"{cluster_result['summary']}\n\n"
                f"📊 **Guud ahaan:** {sent_emoji} ({cluster_result['sentiment']})\n"
                f"*(Waxaan soo koobnay {count} qodob oo muhiim ah)*"
            )
            
            try:
                await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=summary_msg, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Failed to post summary: {e}")
            
            keys_to_delete.append(key)

    # Clean up empty buffers
    for k in keys_to_delete:
        del news_buffer[k]

async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logging.info("🚀 Bot Started (Red/Orange + Speech Summarizer).")
    while True:
        try:
            await process_news_feed(bot)
        except Exception as e:
            logging.error(f"❌ Main Error: {e}")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
