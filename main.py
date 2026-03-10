import os
import time
import re
import asyncio
import logging
import feedparser
import sys
import json
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

# --- IMPORT BANNER GENERATOR ---
try:
    from banner import generate_banner
except ImportError:
    logging.warning("⚠️ banner.py not found — banners disabled.")
    generate_banner = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==================================================================
# 1. INITIALIZE FIREBASE
# ==================================================================
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

# ==================================================================
# 2. ENVIRONMENT VARIABLES
# ==================================================================
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID   = os.getenv("TELEGRAM_CHANNEL_ID")
RSS_URLS_RAW          = os.getenv("RTT_RSS_FEED_URL", "")
OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN")
FACEBOOK_PAGE_ID      = os.getenv("FACEBOOK_PAGE_ID")

# --- MODEL CONFIGURATION ---
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing ENV variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]

# ==================================================================
# 3. NEWS CLASSIFICATION CATEGORIES
# ==================================================================

# Categories that MAY produce market direction signals
MARKET_SIGNAL_CATEGORIES = {"MACRO_DATA", "CENTRAL_BANK", "MONETARY_POLICY"}

# All valid categories
VALID_CATEGORIES = {
    "MACRO_DATA", "CENTRAL_BANK", "MONETARY_POLICY",
    "GEOPOLITICS", "WAR_UPDATE", "CORPORATE",
    "DIPLOMACY", "GENERAL_POLITICS", "NO_MARKET_IMPACT"
}

# Category → smart header mapping
CATEGORY_HEADERS = {
    "MACRO_DATA":        "📊 ECONOMIC DATA UPDATE",
    "CENTRAL_BANK":      "🏦 CENTRAL BANK UPDATE",
    "MONETARY_POLICY":   "💰 MONETARY POLICY UPDATE",
    "GEOPOLITICS":       "🌍 GEOPOLITICAL UPDATE",
    "WAR_UPDATE":        "⚔️ WAR & CONFLICT UPDATE",
    "CORPORATE":         "🏢 CORPORATE NEWS",
    "DIPLOMACY":         "🤝 DIPLOMATIC UPDATE",
    "GENERAL_POLITICS":  "🏛️ POLITICAL UPDATE",
    "NO_MARKET_IMPACT":  "📰 GLOBAL NEWS UPDATE",
}

# Category → banner background color (RGB)
CATEGORY_COLORS = {
    "MACRO_DATA":        (30, 80, 160),     # Blue
    "CENTRAL_BANK":      (140, 20, 20),     # Dark red
    "MONETARY_POLICY":   (100, 20, 100),    # Dark purple
    "GEOPOLITICS":       (90, 50, 140),     # Purple
    "WAR_UPDATE":        (180, 90, 20),     # Dark orange
    "CORPORATE":         (40, 100, 60),     # Green
    "DIPLOMACY":         (50, 90, 130),     # Steel blue
    "GENERAL_POLITICS":  (80, 80, 100),     # Slate gray
    "NO_MARKET_IMPACT":  (100, 100, 100),   # Gray
}

# ==================================================================
# 4. IMPACT & CURRENCY DETECTION (kept from original)
# ==================================================================
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

EXCLUSION_KEYWORDS = [
    "auction", "bid-to-cover", "close", "open",
    "preview", "review", "summary", "poll", "wrap"
]

# ==================================================================
# 5. BUFFERING & BANNER COUNTER
# ==================================================================
news_buffer = {}
BUFFER_TIMEOUT_SECONDS = 300
MAX_BUFFER_SIZE = 10

# Banner insertion counter
post_counter = 0
BANNER_INTERVAL = 7  # Insert a banner every N posts

# ==================================================================
# 6. HELPER FUNCTIONS
# ==================================================================

def get_bot_state():
    try:
        doc = db.collection('bot_state').document('forex_state').get()
        return doc.to_dict() if doc.exists else {"last_link": None, "last_time": 0.0}
    except Exception:
        return {"last_link": None, "last_time": 0.0}


def save_bot_state(last_link, last_time):
    try:
        db.collection('bot_state').document('forex_state').set(
            {"last_link": last_link, "last_time": last_time}, merge=True
        )
    except Exception as e:
        logging.error(f"DB Error: {e}")


def get_flag_and_impact(text):
    flag = None
    impact = None
    detected_currency_code = "USD"

    for k, f in TARGET_CURRENCIES.items():
        if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
            flag = f
            if   f == "🇺🇸": detected_currency_code = "USD"
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
            impact = "🔴"
            break
    if not impact:
        for k in ORANGE_FOLDER_KEYWORDS:
            if re.search(r"\b" + re.escape(k) + r"\b", text, re.IGNORECASE):
                impact = "🟠"
                break

    return flag, impact, detected_currency_code


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


# ==================================================================
# 7. AI ANALYSIS ENGINE (UPGRADED)
# ==================================================================

CLASSIFICATION_SYSTEM_PROMPT = """You are an expert financial news classifier and Forex analyst.

Your job is to:
1. CLASSIFY the news headline into exactly one category.
2. TRANSLATE the headline into clear, natural, professional Somali.
3. Provide a SHORT bullet-point summary (2-3 points) in Somali.
4. ONLY assign market direction if the category is MACRO_DATA, CENTRAL_BANK, or MONETARY_POLICY.

ALLOWED CATEGORIES:
- MACRO_DATA — GDP, CPI, PPI, NFP, unemployment, retail sales, PMI, ISM, housing data, etc.
- CENTRAL_BANK — Fed, ECB, BOE, BOJ, RBA, RBNZ, BOC, SNB announcements, speeches, decisions.
- MONETARY_POLICY — Interest rate changes, QE/QT, forward guidance, dot plot, balance sheet.
- GEOPOLITICS — International tensions, sanctions, trade wars, territorial disputes.
- WAR_UPDATE — Active military conflicts, missile strikes, ceasefire talks, defense news.
- CORPORATE — Earnings, mergers, layoffs, company-specific news.
- DIPLOMACY — Peace talks, diplomatic meetings, treaties, international agreements.
- GENERAL_POLITICS — Elections, legislation, political appointments, domestic policy.
- NO_MARKET_IMPACT — Celebrity, weather, sports, social media, non-financial news.

STRICT FOREX RULES (only for MACRO_DATA, CENTRAL_BANK, MONETARY_POLICY):
- Hawkish / Rate Hikes / Strong Data / Hot Inflation = BULLISH for that currency.
- Dovish / Rate Cuts / Weak Data / Cool Inflation = BEARISH for that currency.
- Mixed or unclear = NEUTRAL.

CRITICAL: If the category is NOT MACRO_DATA, CENTRAL_BANK, or MONETARY_POLICY, you MUST set sentiment to "NONE" and impact to "NONE". Do NOT force a market direction on political, diplomatic, corporate, war, or general news.

You MUST respond in valid JSON only. No markdown, no backticks, no extra text.

Response format:
{
  "category": "CATEGORY_NAME",
  "headline_somali": "Translated headline in Somali",
  "summary_points": [
    "Somali bullet point 1",
    "Somali bullet point 2",
    "Somali bullet point 3"
  ],
  "sentiment": "Bullish" or "Bearish" or "Neutral" or "NONE",
  "currency": "USD" or "EUR" etc or "NONE",
  "reason_somali": "Brief macro explanation in Somali" or "Waxtar toos ah oo suuqa lacagaha ah ma laha.",
  "importance": "High" or "Medium" or "Low" or "NONE",
  "smart_header": "Contextual header text e.g. FED POLICY UPDATE or IRAN WAR UPDATE"
}"""


async def classify_and_analyze(headline: str, currency_code: str = "USD") -> Dict[str, Any]:
    """
    Single AI call that classifies, translates, analyzes, and structures the news.
    """
    default_result = {
        "category": "NO_MARKET_IMPACT",
        "headline_somali": "",
        "summary_points": [],
        "sentiment": "NONE",
        "currency": "NONE",
        "reason_somali": "Waxtar toos ah oo suuqa lacagaha ah ma laha.",
        "importance": "NONE",
        "smart_header": "GLOBAL NEWS UPDATE"
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

            user_content = (
                f"Headline: {headline}\n"
                f"Detected currency context: {currency_code}\n"
                f"Analyze this headline. Respond in JSON only."
            )

            resp = await client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.15,
                max_tokens=500,
            )

            raw_output = resp.choices[0].message.content.strip()

            # Clean potential markdown fences
            raw_output = re.sub(r"^```(?:json)?\s*", "", raw_output)
            raw_output = re.sub(r"\s*```$", "", raw_output)

            data = json.loads(raw_output)

            # Validate category
            cat = data.get("category", "NO_MARKET_IMPACT")
            if cat not in VALID_CATEGORIES:
                cat = "NO_MARKET_IMPACT"
            data["category"] = cat

            # ENFORCE: Non-macro categories must NOT have market signals
            if cat not in MARKET_SIGNAL_CATEGORIES:
                data["sentiment"] = "NONE"
                data["currency"] = "NONE"
                data["importance"] = "NONE"
                data["reason_somali"] = "Waxtar toos ah oo suuqa lacagaha ah ma laha."

            # Apply glossary to Somali text
            data["headline_somali"] = apply_glossary(data.get("headline_somali", ""))
            data["summary_points"] = [apply_glossary(p) for p in data.get("summary_points", [])]
            data["reason_somali"] = apply_glossary(data.get("reason_somali", ""))

            # Fix Trump references
            for key in ["headline_somali", "reason_somali"]:
                data[key] = re.sub(
                    r"Madaxweynihii hore", "Madaxweynaha",
                    data[key], flags=re.IGNORECASE
                )

            return data

    except json.JSONDecodeError as e:
        logging.error(f"❌ AI JSON parse error: {e}")
        return default_result
    except Exception as e:
        logging.error(f"❌ AI analysis error: {e}")
        return default_result


async def summarize_cluster(headlines: List[str], currency_code: str = "USD") -> Dict[str, Any]:
    """
    Summarize a cluster of buffered headlines with the upgraded AI.
    """
    joined = "\n".join(f"- {h}" for h in headlines)

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

            user_content = (
                f"Multiple related headlines about {currency_code}:\n{joined}\n\n"
                f"Classify the overall theme, summarize in Somali, and analyze. "
                f"Respond in JSON only."
            )

            resp = await client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.15,
                max_tokens=600,
            )

            raw_output = resp.choices[0].message.content.strip()
            raw_output = re.sub(r"^```(?:json)?\s*", "", raw_output)
            raw_output = re.sub(r"\s*```$", "", raw_output)

            data = json.loads(raw_output)

            cat = data.get("category", "NO_MARKET_IMPACT")
            if cat not in VALID_CATEGORIES:
                cat = "NO_MARKET_IMPACT"
            data["category"] = cat

            if cat not in MARKET_SIGNAL_CATEGORIES:
                data["sentiment"] = "NONE"
                data["currency"] = "NONE"
                data["importance"] = "NONE"
                data["reason_somali"] = "Waxtar toos ah oo suuqa lacagaha ah ma laha."

            data["headline_somali"] = apply_glossary(data.get("headline_somali", ""))
            data["summary_points"] = [apply_glossary(p) for p in data.get("summary_points", [])]
            data["reason_somali"] = apply_glossary(data.get("reason_somali", ""))

            return data

    except Exception as e:
        logging.error(f"❌ Cluster analysis error: {e}")
        return {
            "category": "NO_MARKET_IMPACT",
            "headline_somali": "",
            "summary_points": ["Warbixin kooban lama heli karo."],
            "sentiment": "NONE",
            "currency": "NONE",
            "reason_somali": "",
            "importance": "NONE",
            "smart_header": "LIVE UPDATE"
        }


# ==================================================================
# 8. MESSAGE FORMATTING
# ==================================================================

def format_message(analysis: Dict[str, Any], flag: str = "", impact_dot: str = "") -> str:
    """
    Build the final structured message from AI analysis results.
    """
    category = analysis.get("category", "NO_MARKET_IMPACT")
    header_emoji = CATEGORY_HEADERS.get(category, "📰 GLOBAL NEWS UPDATE")
    smart_header = analysis.get("smart_header", "NEWS UPDATE")

    # Build header line
    header_line = f"{flag} {header_emoji}" if flag else header_emoji
    if smart_header:
        header_line = f"{flag} {impact_dot} **{smart_header}**" if impact_dot else f"{flag} **{smart_header}**"

    # Build summary bullets
    points = analysis.get("summary_points", [])
    bullet_text = "\n".join(f"  • {p}" for p in points) if points else ""

    # Headline
    headline_som = analysis.get("headline_somali", "")

    # Start building message
    lines = []
    lines.append(header_line)
    lines.append("━━━━━━━━━━━━━━")

    if headline_som:
        lines.append(f"📌 {headline_som}")
        lines.append("")

    if bullet_text:
        lines.append(bullet_text)
        lines.append("")

    # Market analysis section
    if category in MARKET_SIGNAL_CATEGORIES and analysis.get("sentiment") not in ("NONE", None, ""):
        sentiment = analysis.get("sentiment", "Neutral")
        currency = analysis.get("currency", "USD")
        reason = analysis.get("reason_somali", "")
        importance = analysis.get("importance", "Medium")

        # Sentiment emoji
        if "Bullish" in sentiment:
            sent_emoji = "📈"
        elif "Bearish" in sentiment:
            sent_emoji = "📉"
        else:
            sent_emoji = "⚖️"

        # Importance emoji
        if importance == "High":
            imp_emoji = "🔴"
        elif importance == "Medium":
            imp_emoji = "🟠"
        else:
            imp_emoji = "🟡"

        lines.append("📊 **Falanqeynta Suuqa:**")
        lines.append(f"  🎯 Saameynta: {currency} {sent_emoji} ({sentiment})")
        if reason:
            lines.append(f"  💡 Sababta: {reason}")
        lines.append(f"  🚨 Muhiimadda: {importance} {imp_emoji}")
    else:
        # Non-macro: no market signal
        lines.append("📊 **Saameynta Suuqa:** Midna")
        lines.append("  💡 Sababta: Waxtar toos ah oo suuqa lacagaha ah ma laha.")

    return "\n".join(lines)


# ==================================================================
# 9. FACEBOOK HANDLER
# ==================================================================

async def send_to_facebook(text: str, image_path: str = None):
    if not FACEBOOK_ACCESS_TOKEN or not FACEBOOK_PAGE_ID:
        return

    try:
        async with httpx.AsyncClient() as client:
            if image_path and os.path.exists(image_path):
                # Post with image
                url = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/photos"
                with open(image_path, "rb") as img:
                    await client.post(
                        url,
                        data={
                            "caption": strip_markdown(text),
                            "access_token": FACEBOOK_ACCESS_TOKEN
                        },
                        files={"source": ("banner.png", img, "image/png")}
                    )
            else:
                # Text-only post
                url = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/feed"
                await client.post(
                    url,
                    data={
                        "message": strip_markdown(text),
                        "access_token": FACEBOOK_ACCESS_TOKEN
                    }
                )
    except Exception as e:
        logging.error(f"❌ FB Error: {e}")


# ==================================================================
# 10. BANNER INSERTION LOGIC
# ==================================================================

async def maybe_send_banner(bot: Bot, category: str):
    """
    Send a visual banner image if the post counter threshold is reached.
    """
    global post_counter
    post_counter += 1

    if post_counter < BANNER_INTERVAL:
        return
    if generate_banner is None:
        return

    post_counter = 0  # Reset

    header_text = CATEGORY_HEADERS.get(category, "📰 GLOBAL NEWS UPDATE")
    # Strip emoji for banner text
    banner_text = re.sub(r"[^\w\s&\-]", "", header_text).strip().upper()
    if not banner_text:
        banner_text = "MARKET UPDATE"

    color = CATEGORY_COLORS.get(category, (100, 100, 100))

    try:
        image_path = generate_banner(
            text=banner_text,
            bg_color=color,
            output_path="/tmp/banner_latest.png"
        )
        if image_path and os.path.exists(image_path):
            # Send to Telegram
            with open(image_path, "rb") as img:
                await bot.send_photo(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    photo=img,
                    caption=f"━━  {banner_text}  ━━"
                )
            # Send to Facebook
            await send_to_facebook(f"━━  {banner_text}  ━━", image_path=image_path)
            logging.info(f"🖼️ Banner sent: {banner_text}")
    except Exception as e:
        logging.error(f"❌ Banner error: {e}")


# ==================================================================
# 11. MAIN PROCESSING LOGIC
# ==================================================================

async def process_news_feed(bot: Bot):
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
        except Exception:
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

            # BUFFER CHECK (same as before)
            if should_buffer(raw):
                buffer_key = f"{flag}_SPEECH_{cur_code}"
                current_time = time.time()
                if buffer_key not in news_buffer:
                    news_buffer[buffer_key] = {
                        'headlines': [],
                        'start_time': current_time,
                        'currency': cur_code
                    }
                news_buffer[buffer_key]['headlines'].append(clean_title(raw))

                if e.get("link"):
                    latest_link = e.get("link")
                if e.get("published_parsed"):
                    latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))
                continue

            # --- UPGRADED: SINGLE AI CALL FOR CLASSIFICATION + ANALYSIS ---
            logging.info(f"📰 Processing ({cur_code}): {raw}")
            title = clean_title(raw)

            analysis = await classify_and_analyze(title, currency_code=cur_code)

            # Format the structured message
            msg = format_message(analysis, flag=flag, impact_dot=impact)

            # Send to Telegram
            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            except Exception as e:
                logging.error(f"❌ Telegram send error: {e}")

            # Send to Facebook
            await send_to_facebook(msg)

            # Maybe insert a banner
            await maybe_send_banner(bot, analysis.get("category", "NO_MARKET_IMPACT"))

            # Track state
            if e.get("link"):
                latest_link = e.get("link")
            if e.get("published_parsed"):
                latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))

        save_bot_state(latest_link, latest_timestamp)

    # --- PROCESS BUFFERED CLUSTERS ---
    current_time = time.time()
    keys_to_delete = []

    for key, data in news_buffer.items():
        elapsed = current_time - data['start_time']
        count = len(data['headlines'])

        if elapsed > BUFFER_TIMEOUT_SECONDS or count >= MAX_BUFFER_SIZE:
            cur_code = data.get('currency', 'USD')

            # Upgraded cluster analysis
            cluster_result = await summarize_cluster(data['headlines'], currency_code=cur_code)
            flag_emoji = key.split("_")[0]

            # Format the cluster message
            msg = format_message(cluster_result, flag=flag_emoji, impact_dot="📣")

            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=msg,
                    parse_mode="Markdown"
                )
                await send_to_facebook(msg)
            except Exception as e:
                logging.error(f"❌ Cluster post error: {e}")

            # Maybe insert a banner
            await maybe_send_banner(bot, cluster_result.get("category", "NO_MARKET_IMPACT"))

            keys_to_delete.append(key)

    for k in keys_to_delete:
        del news_buffer[k]


# ==================================================================
# 12. ENTRY POINT
# ==================================================================

async def initialize_on_startup(bot: Bot):
    """
    STARTUP FLOOD PREVENTION
    
    On first boot or redeploy:
    1. Fetch all current feed items.
    2. Find the single most recent headline.
    3. Post ONLY that one as a deployment test.
    4. Save its link/timestamp so all older items are permanently skipped.
    
    If Firebase already has a valid state (bot was just restarted, not fresh),
    we still check whether the stored state is stale. If the feed has moved
    far ahead, we fast-forward to the latest item to avoid a flood.
    """
    state = get_bot_state()
    stored_link = state.get("last_link")
    stored_time = state.get("last_time", 0.0)

    # Collect ALL current feed items
    all_items = []
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                pub = e.get("published_parsed")
                ts = time.mktime(pub) if pub else 0.0
                all_items.append((ts, e))
        except Exception:
            pass

    if not all_items:
        logging.info("📭 No feed items found on startup — entering live mode.")
        return

    # Sort by timestamp, newest last
    all_items.sort(key=lambda x: x[0])
    newest_ts, newest_entry = all_items[-1]
    newest_link = newest_entry.get("link")

    # Check if stored state is already current (normal restart, no gap)
    if stored_link == newest_link or stored_time >= newest_ts:
        logging.info("✅ Startup: state is current — no flood risk. Entering live mode.")
        return

    # Count how many items are newer than stored state
    unseen_count = sum(1 for ts, _ in all_items if ts > stored_time)
    logging.info(
        f"⚠️ Startup: {unseen_count} unseen items in feed. "
        f"Skipping history — posting only the latest headline."
    )

    # --- Post ONLY the newest headline as deployment test ---
    raw = newest_entry.title or ""
    if not any(k in raw.lower() for k in EXCLUSION_KEYWORDS):
        flag, impact, cur_code = get_flag_and_impact(raw)
        if flag and impact:
            title = clean_title(raw)
            logging.info(f"🚀 Deployment post ({cur_code}): {title}")

            analysis = await classify_and_analyze(title, currency_code=cur_code)
            msg = format_message(analysis, flag=flag, impact_dot=impact)

            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
                await send_to_facebook(msg)
                logging.info("✅ Deployment test post sent successfully.")
            except Exception as e:
                logging.error(f"❌ Deployment post failed: {e}")

    # --- Fast-forward state to newest item (skip everything older) ---
    save_bot_state(newest_link, newest_ts)
    logging.info(
        f"✅ State fast-forwarded. link={newest_link}, "
        f"time={time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(newest_ts))}"
    )


async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logging.info(f"🚀 HMM News Bot Starting — Model: {AI_MODEL}")

    # --- STEP 1: Startup initialization (prevents history flooding) ---
    try:
        await initialize_on_startup(bot)
    except Exception as e:
        logging.error(f"❌ Startup init error: {e}")

    # --- STEP 2: Live monitoring loop ---
    logging.info("🔄 Entering live monitoring mode...")
    while True:
        try:
            await process_news_feed(bot)
        except Exception as e:
            logging.error(f"❌ Main Error: {e}")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
