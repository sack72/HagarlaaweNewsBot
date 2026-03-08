import os
import time
import re
import asyncio
import logging
import sys
import httpx
from telegram import Bot
from openai import AsyncOpenAI
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

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
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID   = os.getenv("TELEGRAM_CHANNEL_ID")
OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN")
FACEBOOK_PAGE_ID      = os.getenv("FACEBOOK_PAGE_ID")

# --- NEW: FOREXNEWS API ---
FOREXNEWS_API_KEY     = os.getenv("FOREXNEWS_API_KEY")

# --- TEST MODE ---
# Set TEST_MODE=true in Render ENV to:
#   1. Fetch last 10 articles regardless of timestamp (ignore last_forexnews_time)
#   2. Log full API response JSON for debugging
#   3. Skip saving state (so you can re-run and see same articles)
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY, FOREXNEWS_API_KEY]):
    logging.error("Missing ENV variables. Need: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY, FOREXNEWS_API_KEY")
    sys.exit(1)

if TEST_MODE:
    logging.info("⚠️ TEST MODE ENABLED — fetching latest 10 articles, not saving state")

# --- LEGACY RSS (kept as fallback, set RTT_RSS_FEED_URL="" to disable) ---
RSS_URLS_RAW = os.getenv("RTT_RSS_FEED_URL", "")
RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]

# ------------------------------------------------------------------
# 3. FOREXNEWS API CONFIGURATION
# ------------------------------------------------------------------
FOREXNEWS_BASE_URL = "https://forexnewsapi.com/api/v1"

# Currency pairs we care about
TRACKED_PAIRS = [
    "EUR-USD", "GBP-USD", "USD-JPY", "USD-CHF",
    "AUD-USD", "NZD-USD", "USD-CAD", "EUR-GBP",
    "GBP-JPY", "EUR-JPY", "AUD-JPY", "CAD-JPY",
]

# ------------------------------------------------------------------
# 4. IMPACT DEFINITIONS
# ------------------------------------------------------------------
RED_FOLDER_KEYWORDS = [
    "Non-Farm", "NFP", "Unemployment Rate", "CPI", "Interest Rate",
    "Fed Chair", "FOMC", "ECB President", "BOE Governor", "BOJ Governor",
    "GDP", "Retail Sales", "Rate Decision", "Statement", "Monetary Policy",
    "Powell", "Lagarde", "Bailey", "Ueda", "Trump",
    "Rate Cut", "Rate Hike", "Tariff", "Trade War", "Sanctions",
]

ORANGE_FOLDER_KEYWORDS = [
    "PPI", "Producer Price", "Core PCE", "Consumer Confidence",
    "Building Permits", "Housing Starts", "ISM", "PMI", "Trade Balance",
    "JOLTS", "ADP", "Claimant Count", "Zew", "Ifo", "Tankan",
    "Inflation", "Employment", "Manufacturing", "Services",
]

TARGET_CURRENCIES = {
    "USD": "🇺🇸", "US": "🇺🇸", "Fed": "🇺🇸", "FOMC": "🇺🇸", "Powell": "🇺🇸", "Trump": "🇺🇸",
    "EUR": "🇪🇺", "Europe": "🇪🇺", "ECB": "🇪🇺", "Lagarde": "🇪🇺",
    "JPY": "🇯🇵", "Japan": "🇯🇵", "BOJ": "🇯🇵", "Ueda": "🇯🇵",
    "GBP": "🇬🇧", "UK": "🇬🇧", "BOE": "🇬🇧", "Bailey": "🇬🇧",
    "CAD": "🇨🇦", "Canada": "🇨🇦", "BOC": "🇨🇦", "Macklem": "🇨🇦",
    "AUD": "🇦🇺", "Australia": "🇦🇺", "RBA": "🇦🇺", "Bullock": "🇦🇺",
    "NZD": "🇳🇿", "New Zealand": "🇳🇿", "RBNZ": "🇳🇿", "Orr": "🇳🇿",
    "CHF": "🇨🇭", "Swiss": "🇨🇭", "SNB": "🇨🇭", "Jordan": "🇨🇭",
}

CLUSTER_KEYWORDS = [
    "Speech", "Testimony", "Press Conference", "Meeting Minutes",
    "Statement", "Trump", "Powell", "Lagarde", "Bailey", "Ueda", "Q&A",
]

EXCLUSION_KEYWORDS = [
    "auction", "bid-to-cover", "close", "open", "preview",
    "review", "summary", "poll", "wrap",
]

# ------------------------------------------------------------------
# 5. BUFFERING SYSTEM
# ------------------------------------------------------------------
news_buffer = {}
BUFFER_TIMEOUT_SECONDS = 300
MAX_BUFFER_SIZE = 10

# ------------------------------------------------------------------
# 6. HELPER FUNCTIONS
# ------------------------------------------------------------------
def get_bot_state():
    try:
        doc = db.collection('bot_state').document('forex_state').get()
        return doc.to_dict() if doc.exists else {"last_link": None, "last_time": 0.0, "last_forexnews_time": ""}
    except:
        return {"last_link": None, "last_time": 0.0, "last_forexnews_time": ""}

def save_bot_state(last_link=None, last_time=None, last_forexnews_time=None):
    try:
        update_data = {}
        if last_link is not None:
            update_data["last_link"] = last_link
        if last_time is not None:
            update_data["last_time"] = last_time
        if last_forexnews_time is not None:
            update_data["last_forexnews_time"] = last_forexnews_time
        if update_data:
            db.collection('bot_state').document('forex_state').set(update_data, merge=True)
    except Exception as e:
        logging.error(f"DB Error: {e}")

def get_flag_and_impact(text):
    flag = None
    impact = None
    detected_currency_code = "USD"

    for k, f in TARGET_CURRENCIES.items():
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            flag = f
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

def get_flag_from_pair(pair_str):
    """Extract currency code and flag from ForexNewsAPI currency pair like 'EUR-USD'."""
    if not pair_str:
        return None, "USD"
    parts = pair_str.split("-")
    base = parts[0].upper() if parts else "USD"
    for k, f in TARGET_CURRENCIES.items():
        if k.upper() == base:
            return f, base
    return "🇺🇸", "USD"

def should_buffer(text):
    for k in CLUSTER_KEYWORDS:
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            return True
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
# 7. FOREXNEWS API FETCHERS
# ------------------------------------------------------------------
async def fetch_forexnews_articles(since_date: str = None) -> List[Dict]:
    """
    Fetch latest forex news articles from ForexNewsAPI.
    In TEST_MODE: fetches 10 articles, ignores since_date, logs raw JSON.
    In PRODUCTION: fetches 20 articles, filters by since_date.
    """
    items_count = 10 if TEST_MODE else 20
    params = {
        "token": FOREXNEWS_API_KEY,
        "items": items_count,
    }
    url = f"{FOREXNEWS_BASE_URL}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            # --- DEBUG: Log raw response structure ---
            if TEST_MODE:
                logging.info(f"🔍 DEBUG — Raw API response keys: {list(data.keys())}")
                # Log first article structure so you can see field names
                articles_raw = data.get("data", [])
                if not articles_raw:
                    # Try alternative keys the API might use
                    for possible_key in ["articles", "results", "news", "items"]:
                        articles_raw = data.get(possible_key, [])
                        if articles_raw:
                            logging.info(f"🔍 DEBUG — Found articles under key: '{possible_key}'")
                            break
                    if not articles_raw and isinstance(data, list):
                        articles_raw = data
                        logging.info(f"🔍 DEBUG — Response is a direct list of {len(articles_raw)} items")

                if articles_raw:
                    first = articles_raw[0]
                    logging.info(f"🔍 DEBUG — First article keys: {list(first.keys())}")
                    logging.info(f"🔍 DEBUG — First article sample:")
                    logging.info(f"    title: {first.get('title', 'N/A')}")
                    logging.info(f"    date: {first.get('date', first.get('published_at', first.get('pubDate', 'N/A')))}")
                    logging.info(f"    sentiment: {first.get('sentiment', 'N/A')}")
                    logging.info(f"    currency_pair: {first.get('currency_pair', first.get('currencyPair', first.get('currencies', 'N/A')))}")
                    logging.info(f"    source: {first.get('source_name', first.get('source', 'N/A'))}")
                    logging.info(f"    news_url: {first.get('news_url', first.get('url', 'N/A'))}")
                else:
                    logging.warning(f"⚠️ DEBUG — No articles found! Full response (first 500 chars): {str(data)[:500]}")

                logging.info(f"📡 ForexNewsAPI returned {len(articles_raw)} articles (TEST MODE — no time filter)")
                return articles_raw

            # --- PRODUCTION MODE ---
            articles = data.get("data", [])
            logging.info(f"📡 ForexNewsAPI returned {len(articles)} articles")

            # Filter by timestamp
            if since_date:
                articles = [
                    a for a in articles
                    if a.get("date", "") > since_date
                ]
                logging.info(f"📰 {len(articles)} new articles after filtering")

            return articles

    except httpx.HTTPStatusError as e:
        logging.error(f"❌ ForexNewsAPI HTTP Error: {e.response.status_code} - {e.response.text}")
        return []
    except Exception as e:
        logging.error(f"❌ ForexNewsAPI Error: {e}")
        return []

async def fetch_forexnews_by_pair(pair: str) -> List[Dict]:
    """Fetch news filtered by a specific currency pair like 'EUR-USD'."""
    params = {
        "token": FOREXNEWS_API_KEY,
        "currencypair": pair,
        "items": 10,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(FOREXNEWS_BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
    except Exception as e:
        logging.error(f"❌ ForexNewsAPI Pair Error ({pair}): {e}")
        return []

async def fetch_economic_calendar() -> List[Dict]:
    """
    Fetch today's high-importance economic calendar events.
    Returns events with: title, country, date, importance, actual, forecast, previous
    """
    params = {
        "token": FOREXNEWS_API_KEY,
        "date": "today",
        "importance": "high",
    }
    url = f"{FOREXNEWS_BASE_URL}/economic-calendar"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            events = data.get("data", [])
            logging.info(f"📅 Economic Calendar: {len(events)} high-impact events today")
            return events
    except Exception as e:
        logging.error(f"❌ Economic Calendar Error: {e}")
        return []

async def fetch_sentiment_data() -> Dict:
    """Fetch overall forex market sentiment from ForexNewsAPI."""
    params = {
        "token": FOREXNEWS_API_KEY,
    }
    url = f"{FOREXNEWS_BASE_URL}/sentiment"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logging.error(f"❌ Sentiment API Error: {e}")
        return {}

# ------------------------------------------------------------------
# 8. API HANDLERS (AI & Facebook)
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

async def analyze_single_news(text, currency_code="USD", api_sentiment=None):
    """
    Analyze a single news headline with AI.
    Now also receives the ForexNewsAPI sentiment as extra context.
    """
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

            sentiment_hint = ""
            if api_sentiment:
                sentiment_hint = f" The news source rates this as '{api_sentiment}' sentiment."

            system_prompt = (
                f"You are a Professional Forex Analyst focusing strictly on {currency_code}. "
                f"Analyze the headline for its impact on THIS currency specifically.{sentiment_hint} "
                "STRICT FOREX RULES:"
                "1. Rate Hikes / Hawkish / Strong Inflation / Good Data = BULLISH."
                "2. Rate Cuts / Dovish / Weak Inflation / Bad Data = BEARISH."
                "3. If the news is about JPY, analyze JPY impact. If about EUR, analyze EUR impact."
                "Output format: Sentiment: [Bullish/Bearish] | Asset: [{currency_code}] | Reason: [Brief explanation in SOMALI] | Impact: [High/Med/Low]"
            )
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
            )
            out = resp.choices[0].message.content.strip()

            data = {"sentiment": "Neutral", "asset": currency_code, "reason": "", "impact": "Med"}
            parts = out.split("|")
            for p in parts:
                if "Sentiment:" in p: data["sentiment"] = p.replace("Sentiment:", "").strip()
                if "Asset:" in p: data["asset"] = p.replace("Asset:", "").strip()
                if "Reason:" in p: data["reason"] = p.replace("Reason:", "").strip()
                if "Impact:" in p: data["impact"] = p.replace("Impact:", "").strip()
            return data
    except:
        return {"sentiment": "Neutral", "asset": currency_code, "reason": "", "impact": "Med"}

async def translate_to_somali(text):
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            sys_msg = "Translate to Somali. Financial context. Keep it professional."
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": text}]
            )
            res = apply_glossary(resp.choices[0].message.content.strip())
            return re.sub(r"Madaxweynihii hore", "Madaxweynaha", res, flags=re.IGNORECASE)
    except:
        return ""

# ------------------------------------------------------------------
# 9. PROCESS FOREXNEWS API ARTICLES
# ------------------------------------------------------------------
async def process_forexnews_feed(bot: Bot):
    """Main processor for ForexNewsAPI articles."""
    state = get_bot_state()
    last_forexnews_time = state.get("last_forexnews_time", "")

    # TEST MODE: ignore saved timestamp — always fetch latest
    if TEST_MODE:
        last_forexnews_time = ""
        logging.info("🧪 TEST MODE — ignoring saved timestamp, fetching latest articles")

    articles = await fetch_forexnews_articles(
        since_date=last_forexnews_time if last_forexnews_time else None
    )

    if not articles:
        return

    # Sort by date ascending (oldest first) so we process in order
    articles.sort(key=lambda a: a.get("date", ""))

    latest_time = last_forexnews_time

    for article in articles:
        title = article.get("title", "")
        news_url = article.get("news_url", "")
        source = article.get("source_name", "")
        api_sentiment = article.get("sentiment", "")  # positive/negative/neutral from API
        article_date = article.get("date", "")
        currency_pairs = article.get("currency_pair", [])  # May be list or string

        # Normalize currency_pairs to a list
        if isinstance(currency_pairs, str):
            currency_pairs = [currency_pairs] if currency_pairs else []

        # Skip exclusion keywords
        if any(k in title.lower() for k in EXCLUSION_KEYWORDS):
            continue

        # --- DETERMINE CURRENCY & FLAG ---
        # Priority 1: Use currency pair from API if available
        if currency_pairs:
            pair = currency_pairs[0]  # Primary pair
            flag, cur_code = get_flag_from_pair(pair)
        else:
            # Priority 2: Fall back to keyword detection
            flag, impact_unused, cur_code = get_flag_and_impact(title)

        # --- DETERMINE IMPACT LEVEL ---
        flag_kw, impact, cur_code_kw = get_flag_and_impact(title)
        if not flag:
            flag = flag_kw
            cur_code = cur_code_kw

        # Skip if we can't determine flag or impact
        if not flag or not impact:
            continue

        # --- BUFFER CHECK (for speeches/clusters) ---
        if should_buffer(title):
            buffer_key = f"{flag}_SPEECH_{cur_code}"
            current_time = time.time()
            if buffer_key not in news_buffer:
                news_buffer[buffer_key] = {
                    'headlines': [],
                    'start_time': current_time,
                    'currency': cur_code,
                }
            news_buffer[buffer_key]['headlines'].append(clean_title(title))
            if article_date > latest_time:
                latest_time = article_date
            continue

        # --- STANDARD PROCESSING ---
        logging.info(f"📰 ForexNewsAPI ({cur_code}): {title}")
        clean = clean_title(title)
        somali = await translate_to_somali(clean)

        # Pass API sentiment as hint to AI analysis
        analysis = await analyze_single_news(clean, currency_code=cur_code, api_sentiment=api_sentiment)

        # --- FORMAT MESSAGE ---
        if impact == "🔴":
            sent_emoji = "📈" if "Bullish" in analysis['sentiment'] else "📉"
            if "Neutral" in analysis['sentiment']:
                sent_emoji = "⚖️"

            # Map API sentiment to Somali label
            api_sent_label = ""
            if api_sentiment == "positive":
                api_sent_label = "✅ Wanaagsan"
            elif api_sentiment == "negative":
                api_sent_label = "⛔ Xun"
            elif api_sentiment == "neutral":
                api_sent_label = "⚖️ Dhexdhexaad"

            msg = (
                f"{flag} {impact} **{somali}**\n"
                f"━━━━━━━━━━━━━━\n"
                f"📊 **Falanqeynta Suuqa:**\n"
                f"🎯 **Saameynta:** {analysis['asset']} {sent_emoji} ({analysis['sentiment']})\n"
                f"💡 **Sababta:** {analysis['reason']}\n"
                f"🚨 **Muhiimadda:** High 🔴\n"
            )
            if api_sent_label:
                msg += f"📡 **API Sentiment:** {api_sent_label}\n"
            if source:
                msg += f"🔗 _{source}_"
        else:
            msg = f"{flag} {impact} **{somali}**"
            if source:
                msg += f"\n🔗 _{source}_"

        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            await send_to_facebook(msg)
        except Exception as e:
            logging.error(f"❌ Telegram Error: {e}")

        if article_date > latest_time:
            latest_time = article_date

    # Save the latest processed timestamp (skip in TEST MODE)
    if latest_time and latest_time != last_forexnews_time:
        if TEST_MODE:
            logging.info(f"🧪 TEST MODE — would save timestamp: {latest_time} (skipped)")
        else:
            save_bot_state(last_forexnews_time=latest_time)

# ------------------------------------------------------------------
# 10. ECONOMIC CALENDAR ALERT (runs less frequently)
# ------------------------------------------------------------------
async def process_economic_calendar(bot: Bot):
    """Post upcoming high-impact economic events."""
    events = await fetch_economic_calendar()
    if not events:
        return

    now = datetime.now(timezone.utc)

    for event in events[:5]:  # Limit to top 5
        title = event.get("title", "Unknown Event")
        country = event.get("country", "")
        event_date = event.get("date", "")
        importance = event.get("importance", "")
        actual = event.get("actual", "—")
        forecast = event.get("forecast", "—")
        previous = event.get("previous", "—")

        # Get flag for country
        flag = "🌐"
        for k, f in TARGET_CURRENCIES.items():
            if k.upper() in country.upper():
                flag = f
                break

        # Only post if actual data just came out (has value)
        if actual and actual != "—":
            somali_title = await translate_to_somali(title)

            # Determine if beat/miss
            beat_miss = ""
            try:
                act_num = float(str(actual).replace("%", "").replace("K", "").replace("M", ""))
                fore_num = float(str(forecast).replace("%", "").replace("K", "").replace("M", ""))
                if act_num > fore_num:
                    beat_miss = "📈 **Ka Wanaagsan Saadaalka!**"
                elif act_num < fore_num:
                    beat_miss = "📉 **Ka Xun Saadaalka!**"
                else:
                    beat_miss = "⚖️ **La mid Saadaalka**"
            except:
                pass

            msg = (
                f"{flag} 📅 **XOGTA DHAQAALAHA**\n"
                f"━━━━━━━━━━━━━━\n"
                f"📌 **{somali_title}**\n"
                f"📊 Natiijada: **{actual}** | Saadaal: {forecast} | Horay: {previous}\n"
            )
            if beat_miss:
                msg += f"{beat_miss}\n"

            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logging.error(f"❌ Calendar Telegram Error: {e}")

# ------------------------------------------------------------------
# 11. LEGACY RSS PROCESSOR (kept as fallback)
# ------------------------------------------------------------------
import feedparser

async def process_rss_feed(bot: Bot):
    """Legacy RSS feed processor — only runs if RTT_RSS_FEED_URL is set."""
    if not RSS_URLS:
        return

    state = get_bot_state()
    last_link = state.get('last_link')
    last_time = state.get('last_time', 0.0)

    new_items = []
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                if e.get("link") == last_link:
                    break
                pub = e.get("published_parsed")
                if pub and time.mktime(pub) <= last_time:
                    continue
                new_items.append(e)
        except:
            pass

    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())

    if new_items:
        latest_timestamp = last_time
        latest_link = last_link

        for e in new_items:
            raw = e.title or ""
            if any(k in raw.lower() for k in EXCLUSION_KEYWORDS):
                continue
            flag, impact, cur_code = get_flag_and_impact(raw)
            if not flag or not impact:
                continue
            if should_buffer(raw):
                buffer_key = f"{flag}_SPEECH_{cur_code}"
                current_time = time.time()
                if buffer_key not in news_buffer:
                    news_buffer[buffer_key] = {'headlines': [], 'start_time': current_time, 'currency': cur_code}
                news_buffer[buffer_key]['headlines'].append(clean_title(raw))
                if e.get("link"): latest_link = e.get("link")
                if e.get("published_parsed"): latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))
                continue
            logging.info(f"📰 RSS ({cur_code}): {raw}")
            title = clean_title(raw)
            somali = await translate_to_somali(title)
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

        save_bot_state(last_link=latest_link, last_time=latest_timestamp)

# ------------------------------------------------------------------
# 12. BUFFER PROCESSOR
# ------------------------------------------------------------------
async def process_buffers(bot: Bot):
    """Process buffered speech/cluster headlines."""
    current_time = time.time()
    keys_to_delete = []

    for key, data in news_buffer.items():
        elapsed = current_time - data['start_time']
        count = len(data['headlines'])

        if elapsed > BUFFER_TIMEOUT_SECONDS or count >= MAX_BUFFER_SIZE:
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

# ------------------------------------------------------------------
# 13. MAIN LOOP
# ------------------------------------------------------------------
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    if TEST_MODE:
        logging.info("🚀 Bot Started — TEST MODE (single run, then exit)")
        logging.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        try:
            await process_forexnews_feed(bot)
            await process_buffers(bot)
            await process_economic_calendar(bot)
        except Exception as e:
            logging.error(f"❌ Test Error: {e}")
            import traceback
            traceback.print_exc()
        logging.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logging.info("✅ TEST RUN COMPLETE — set TEST_MODE=false for production")
        return  # Exit after single run

    # --- PRODUCTION MODE ---
    logging.info("🚀 Bot Started — ForexNewsAPI + RSS Hybrid Mode (PRODUCTION)")

    loop_count = 0
    calendar_interval = 10  # Check calendar every 10 loops (10 minutes)

    while True:
        try:
            # --- PRIMARY: ForexNewsAPI ---
            await process_forexnews_feed(bot)

            # --- FALLBACK: Legacy RSS (if configured) ---
            await process_rss_feed(bot)

            # --- BUFFERS ---
            await process_buffers(bot)

            # --- ECONOMIC CALENDAR (less frequent) ---
            loop_count += 1
            if loop_count % calendar_interval == 0:
                await process_economic_calendar(bot)
                loop_count = 0

        except Exception as e:
            logging.error(f"❌ Main Error: {e}")

        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
