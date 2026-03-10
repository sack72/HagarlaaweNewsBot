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
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN")
FACEBOOK_PAGE_ID      = os.getenv("FACEBOOK_PAGE_ID")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing ENV variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]

# ------------------------------------------------------------------
# 3. IMPACT DEFINITIONS
# ------------------------------------------------------------------

RED_FOLDER_KEYWORDS = [
    "Non-Farm", "NFP", "Unemployment Rate", "CPI", "Interest Rate", 
    "Fed Chair", "FOMC", "ECB President", "BOE Governor", "BOJ Governor", 
    "GDP", "Retail Sales", "Rate Decision", "Statement", "Monetary Policy",
    "Powell", "Lagarde", "Bailey", "Ueda", "Trump"
]

ORANGE_FOLDER_KEYWORDS = [
    "PPI", "Producer Price", "Core PCE", "Consumer Confidence", 
    "Building Permits", "Housing Starts", "ISM", "PMI", "Trade Balance", 
    "JOLTS", "ADP", "Claimant Count", "Zew", "Ifo", "Tankan"
]

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

CLUSTER_KEYWORDS = [
    "Speech", "Testimony", "Press Conference", "Meeting Minutes", 
    "Statement", "Trump", "Powell", "Lagarde", "Bailey", "Ueda", "Q&A"
]

EXCLUSION_KEYWORDS = ["auction", "bid-to-cover", "close", "open", "preview", "review", "summary", "poll", "wrap"]

# ------------------------------------------------------------------
# 4. BUFFERING SYSTEM
# ------------------------------------------------------------------
news_buffer = {}
BUFFER_TIMEOUT_SECONDS = 300 
MAX_BUFFER_SIZE = 10 

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
    detected_currency_code = "USD" # Default

    # Detect Currency Code & Flag
    for k, f in TARGET_CURRENCIES.items():
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            flag = f
            # Map keywords to Currency Codes
            if f == "🇺🇸": detected_currency_code = "USD"
            elif f == "🇪🇺": detected_currency_code = "EUR"
            elif f == "🇯🇵": detected_currency_code = "JPY"
            elif f == "🇬🇧": detected_currency_code = "GBP"
            elif f == "🇨🇦": detected_currency_code = "CAD"
            elif f == "🇦🇺": detected_currency_code = "AUD"
            elif f == "🇳🇿": detected_currency_code = "NZD"
            elif f == "🇨🇭": detected_currency_code = "CHF"
            break
            
    for k in RED_FOLDER_KEYWORDS:
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            impact = "🔴"; break
    if not impact:
        for k in ORANGE_FOLDER_KEYWORDS:
            if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
                impact = "🟠"; break
    return flag, impact, detected_currency_code

def should_buffer(text):
    for k in CLUSTER_KEYWORDS:
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE): return True
    return False

def clean_title(t):
    t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}:?\s*", "", t)
    t = re.sub(r"^[^:]+:\s*", "", t).strip()
    return t

def apply_glossary(text):
    text = re.sub(r"Aqalka Cad", "AQALKA_TEMP_PLACEHOLDER", text, flags=re.IGNORECASE)
    for eng, som in GLOSSARY.items():
        pattern = re.compile(r"\b" + re.escape(eng) + r"\b", re.IGNORECASE)
        text = pattern.sub(som, text)
    text = text.replace("AQALKA_TEMP_PLACEHOLDER", "Aqalka Cad")
    return text

def strip_markdown(text):
    return text.replace("**", "").replace("__", "")

# ------------------------------------------------------------------
# 6. API HANDLERS (AI & Facebook)
# ------------------------------------------------------------------
async def send_to_facebook(text):
    if not FACEBOOK_ACCESS_TOKEN or not FACEBOOK_PAGE_ID:
        return
    clean_text = strip_markdown(text)
    url = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/feed"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, data={"message": clean_text, "access_token": FACEBOOK_ACCESS_TOKEN})
    except Exception as e:
        logging.error(f"❌ FB Connection Error: {e}")

async def summarize_cluster(headlines: List[str], currency_code: str = "USD") -> Dict[str, Any]:
    joined_text = "\n".join(headlines)
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            
            # --- UPDATED PROMPT: FOREX LOGIC ---
            system_prompt = (
                f"You are a Forex Trading Algorithm focusing on {currency_code}. "
                "RULES: "
                "1. DOVISH (Rate Cuts, Easy Money) = BEARISH for Currency. "
                "2. HAWKISH (Rate Hikes, Tight Money) = BULLISH for Currency. "
                "3. Summarize key takeaways in 2-3 Somali bullet points. "
                "Output format: Sentiment: [Bullish/Bearish] | Summary: [Somali Text]"
            )
            
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": joined_text}],
                temperature=0.2, max_tokens=250,
            )
            out = resp.choices[0].message.content.strip()
            sentiment = "Neutral"
            summary = out
            if "Sentiment:" in out and "|" in out:
                parts = out.split("|")
                sentiment = parts[0].replace("Sentiment:", "").strip()
                summary = parts[1].replace("Summary:", "").strip()
            return {"sentiment": sentiment, "summary": apply_glossary(summary)}
    except Exception:
        return {"sentiment": "Neutral", "summary": "Warbixin kooban lama heli karo."}

async def analyze_single_news(text, currency_code="USD"):
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            
            # --- CRITICAL FIX: FOREX LOGIC & CURRENCY LOCK ---
            system_prompt = (
                f"You are a Professional Forex Analyst focusing strictly on {currency_code}. "
                "Analyze the headline for its impact on THIS currency specifically. "
                "STRICT FOREX RULES:"
                "1. Rate Hikes / Hawkish / Strong Inflation / Good Data = BULLISH."
                "2. Rate Cuts / Dovish / Weak Inflation / Bad Data = BEARISH."
                "3. If the news is about JPY, analyze JPY impact. If about EUR, analyze EUR impact."
                "Output format: Sentiment: [Bullish/Bearish] | Asset: [{currency_code}] | Reason: [Brief explanation in SOMALI] | Impact: [High/Med/Low]"
            )
            
            resp = await client.chat.completions.create(
                model="gpt-4o-mini", 
                messages=[{"role":"system","content":system_prompt},{"role":"user","content":text}]
            )
            out = resp.choices[0].message.content.strip()
            
            data = {"sentiment":"Neutral", "asset":currency_code, "reason":"", "impact":"Med"}
            parts = out.split("|")
            for p in parts:
                if "Sentiment:" in p: data["sentiment"] = p.replace("Sentiment:", "").strip()
                if "Asset:" in p: data["asset"] = p.replace("Asset:", "").strip()
                if "Reason:" in p: data["reason"] = p.replace("Reason:", "").strip()
                if "Impact:" in p: data["impact"] = p.replace("Impact:", "").strip()
            return data
    except:
        return {"sentiment":"Neutral", "asset":currency_code, "reason":"", "impact":"Med"}

async def translate_to_somali(text):
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            sys_msg = "Translate to Somali. Financial context. Keep it professional."
            resp = await client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"system","content":sys_msg},{"role":"user","content":text}])
            res = apply_glossary(resp.choices[0].message.content.strip())
            return re.sub(r"Madaxweynihii hore", "Madaxweynaha", res, flags=re.IGNORECASE)
    except:
        return ""

# ------------------------------------------------------------------
# 7. MAIN LOGIC
# ------------------------------------------------------------------
async def process_news_feed(bot: Bot):
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
        except: pass
    
    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())
    
    if new_items:
        latest_timestamp = last_time
        latest_link = last_link

        for e in new_items:
            raw = e.title or ""
            if any(k in raw.lower() for k in EXCLUSION_KEYWORDS): continue
            
            # --- FIX: GET CURRENCY CODE ---
            flag, impact, cur_code = get_flag_and_impact(raw)
            if not flag or not impact: continue

            # BUFFER CHECK
            if should_buffer(raw):
                buffer_key = f"{flag}_SPEECH_{cur_code}" # Add code to key
                current_time = time.time()
                if buffer_key not in news_buffer:
                    news_buffer[buffer_key] = {'headlines': [], 'start_time': current_time, 'currency': cur_code}
                news_buffer[buffer_key]['headlines'].append(clean_title(raw))
                
                if e.get("link"): latest_link = e.get("link")
                if e.get("published_parsed"): latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))
                continue

            # STANDARD PROCESSING
            logging.info(f"📰 Processing ({cur_code}): {raw}")
            title = clean_title(raw)
            somali = await translate_to_somali(title)
            
            # --- FIX: PASS CURRENCY CODE TO AI ---
            analysis = await analyze_single_news(title, currency_code=cur_code)
            
            if impact == "🔴":
                sent_emoji = "📈" if "Bullish" in analysis['sentiment'] else "📉"
                if "Neutral" in analysis['sentiment']: sent_emoji = "⚖️"

                msg = (
                    f"{flag} {impact} **{somali}**\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📊 **Falanqeynta Suuqa:**\n"
                    f"🎯 **Saameynta:** {analysis['asset']} {sent_emoji} ({analysis['sentiment']})\n"
                    f"💡 **Sababta:** {analysis['reason']}\n"
                    f"🚨 **Muhiimadda:** High 🔴"
                )
            else:
                msg = f"{flag} {impact} **{somali}**"

            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
            await send_to_facebook(msg)
            
            if e.get("link"): latest_link = e.get("link")
            if e.get("published_parsed"): latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))

        save_bot_state(latest_link, latest_timestamp)

    # PROCESS BUFFERS
    current_time = time.time()
    keys_to_delete = []

    for key, data in news_buffer.items():
        elapsed = current_time - data['start_time']
        count = len(data['headlines'])
        
        if elapsed > BUFFER_TIMEOUT_SECONDS or count >= MAX_BUFFER_SIZE:
            # Extract currency from stored buffer data
            cur_code = data.get('currency', 'USD')
            
            cluster_result = await summarize_cluster(data['headlines'], currency_code=cur_code)
            flag_emoji = key.split("_")[0] 
            
            is_high_impact = any(k in " ".join(data['headlines']) for k in RED_FOLDER_KEYWORDS)

            sent_emoji = "⚖️"
            if "Bullish" in cluster_result['sentiment']: sent_emoji = "📈"
            elif "Bearish" in cluster_result['sentiment']: sent_emoji = "📉"

            summary_msg = (
                f"{flag_emoji} 📣 **WARBIXIN KOOBAN (Live Update)**\n"
                f"━━━━━━━━━━━━━━\n"
                f"{cluster_result['summary']}\n"
            )

            if is_high_impact:
                summary_msg += f"\n📊 **Guud ahaan:** {cur_code} {sent_emoji} ({cluster_result['sentiment']})"
            
            try:
                await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=summary_msg, parse_mode="Markdown")
                await send_to_facebook(summary_msg)
            except Exception as e:
                logging.error(f"Failed to post summary: {e}")
            
            keys_to_delete.append(key)

    for k in keys_to_delete:
        del news_buffer[k]

async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logging.info("🚀 Bot Started (Forex Logic Fix Applied).")
    while True:
        try:
            await process_news_feed(bot)
        except Exception as e:
            logging.error(f"❌ Main Error: {e}")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
