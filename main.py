import os
import time
import re
import asyncio
import logging
import feedparser
from telegram import Bot
from openai import AsyncOpenAI
import httpx
import sys
from typing import Optional, Any

# Import the full glossary
from glossary import GLOSSARY

###############################################################################
# 1. Environment & Setup
###############################################################################
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
RSS_URLS_RAW        = os.getenv("RTT_RSS_FEED_URL", "")

FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
FACEBOOK_PAGE_ID           = os.getenv("FACEBOOK_PAGE_ID")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing required environment variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

###############################################################################
# 2. Persistent Storage
###############################################################################
PERSISTENT_STORAGE_PATH = "/bot-data"
LAST_LINK_FILE          = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")
LAST_TIME_FILE          = os.path.join(PERSISTENT_STORAGE_PATH, "last_published_time.txt")

def load_last_posted_link() -> Optional[str]:
    if os.path.isfile(LAST_LINK_FILE):
        try:
            with open(LAST_LINK_FILE, "r") as f:
                return f.readline().strip() or None
        except IOError:
            return None
    return None

def save_last_posted_link(link: str) -> None:
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_LINK_FILE, "w") as f:
        f.write(link)

def load_last_time() -> float:
    if os.path.isfile(LAST_TIME_FILE):
        try:
            with open(LAST_TIME_FILE, "r") as f:
                return float(f.read().strip())
        except Exception:
            return 0.0
    return 0.0

def save_last_time(timestamp: float) -> None:
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_TIME_FILE, "w") as f:
        f.write(str(timestamp))

###############################################################################
# 3. Somali Glossary & Translation
###############################################################################
def apply_glossary(text: str) -> str:
    """Replace key English financial terms with consistent Somali equivalents."""
    for eng, som in GLOSSARY.items():
        pattern = re.compile(r"\b" + re.escape(eng) + r"\b", re.IGNORECASE)
        text = pattern.sub(som, text)
    return text

async def translate_to_somali(text: str) -> str:
    """
    Translate headline into professional Somali financial-news style.
    Uses glossary afterwards to normalize technical terms.
    """
    try:
        logging.info(f"Translating: {text}")
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            resp = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Waxaad tahay weriye dhaqaale oo Somali ah. "
                            "U turjun cinwaanka si kooban, cad oo xirfad leh, "
                            "kana dhig qaab wararka maaliyadda. "
                            "Marka aad tixraacayso Donald Trump isticmaal: "
                            "\"Madaxweyne Donald Trump\"."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
                max_tokens=250,
            )
            somali = resp.choices[0].message.content.strip()
            somali = apply_glossary(somali)
            return somali
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        return ""

###############################################################################
# 4. Sentiment
###############################################################################
async def analyze_sentiment(text: str):
    """
    Returns: (Tone, Horizon, Confidence%)
    Tone: Bullish/Bearish/Neutral
    Horizon: Intraday/Short-term/Medium-term/Macro
    Confidence: 0–100
    """
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            resp = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a conservative FX & macro analyst. "
                            "For the given headline, return: "
                            "Tone (Bullish/Bearish/Neutral), Horizon, Confidence (0-100). "
                            "Format strictly as: Tone: X; Horizon: Y; Confidence: Z"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=32,
            )
            out = resp.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Sentiment analysis failed: {e}")
        return ("Neutral", "Unknown", 50)

    tone_match  = re.search(r"(Bullish|Bearish|Neutral)", out, re.IGNORECASE)
    horiz_match = re.search(r"(Intraday|Short-term|Medium-term|Macro)", out, re.IGNORECASE)
    conf_match  = re.search(r"Confidence:\s*(\d+)", out)

    tone  = tone_match.group(1).capitalize() if tone_match else "Neutral"
    horiz = horiz_match.group(1).capitalize() if horiz_match else "Unknown"

    try:
        conf = int(conf_match.group(1)) if conf_match else 50
    except Exception:
        conf = 50
    conf = max(0, min(conf, 100))

    return (tone, horiz, conf)

###############################################################################
# 5. Facebook Posting
###############################################################################
async def post_to_facebook(message: str) -> None:
    """Cross-post to Facebook page if credentials are present."""
    if not FACEBOOK_PAGE_ACCESS_TOKEN or not FACEBOOK_PAGE_ID:
        return

    hashtags = "\n\n#HagarlaaweHMM #WararkaFx #Forexsomali #Dhaqaalaha #Maaliyadda"
    fb_url = f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}/feed"
    data = {
        "message": message + hashtags,
        "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(fb_url, data=data)
            if r.status_code == 200:
                logging.info("✅ Posted to Facebook successfully.")
            else:
                logging.error(f"❌ Facebook post failed: {r.status_code} - {r.text}")
    except Exception as e:
        logging.error(f"Facebook error: {e}")

###############################################################################
# 6. Filtering (Powell-only, Macro-only, No noise)
###############################################################################

# Powell is the only Fed voice allowed
POWELL_ONLY = [
    "jerome powell",
    "powell",
    "fed chair powell",
]

# Block low-impact ECB/BOE/EU/Fed speakers
BLOCKED_SPEAKERS = [
    "de guindos", "makhlouf", "sleijpen", "merz",
    "mann", "wells", "he lifeng",
    "jefferson", "harker", "barkin", "goolsbee", "mester",
    "logan", "cook", "daly", "collins", "barr", "kashkari",
]

# Block T-bill auctions (noise)
BLOCKED_LOW_IMPACT_BOND = [
    "3-month", "3 month",
    "6-month", "6 month",
    "t-bill", "tbill", "t bill",
    "bill auction",
    "treasury bill",
    "bond auction",
    "auction results",
    "bid-to-cover",
]

# Block noisy sources by name
BLOCKED_SOURCES = [
    "financialjuice",
    "financial juice",
    "financialnews",
    "financial news",
]

# Allow only true macro movers
HIGH_IMPACT_MACRO = [
    "cpi", "inflation", "pce", "core pce",
    "core cpi", "nfp", "nonfarm", "unemployment",
    "gdp", "retail sales", "ppi",
    "pmi", "ism",
    "yield", "treasury yield", "yields",
    "risk-off", "risk on", "risk-on",
    "fomc", "fed policy", "federal reserve",
    "market crash", "selloff", "sell-off", "volatility",
    "white house", "biden", "madaxweyne donald trump",
]

def contains(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords)

def is_powell(text: str) -> bool:
    return contains(text, POWELL_ONLY)

def is_blocked_speaker(text: str) -> bool:
    return contains(text, BLOCKED_SPEAKERS)

def is_low_impact_bond(text: str) -> bool:
    return contains(text, BLOCKED_LOW_IMPACT_BOND)

def is_blocked_source(text: str) -> bool:
    return contains(text, BLOCKED_SOURCES)

def is_high_macro(text: str) -> bool:
    return contains(text, HIGH_IMPACT_MACRO)

###############################################################################
# 7. Fetch & Post Loop
###############################################################################
async def fetch_and_post(bot: Bot):
    last_link = load_last_posted_link()
    last_time = load_last_time()
    new_items: list[Any] = []

    for url in RSS_URLS:
        logging.info(f"🔄 Fetching feed: {url}")
        feed = feedparser.parse(url)

        for e in feed.entries:
            link = e.get("link")
            pub  = e.get("published_parsed")

            if link == last_link:
                break
            if pub and time.mktime(pub) <= last_time:
                continue

            new_items.append(e)

    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())

    if not new_items:
        logging.info("📭 No new items.")
        return

    latest_timestamp = last_time

    for e in new_items:
        title = e.title or ""
        t = title.lower()

        # 1) Block noisy sources
        if is_blocked_source(t):
            logging.info(f"❌ BLOCKED SOURCE: {title}")
            continue

        # 2) Block low-impact bond auctions
        if is_low_impact_bond(t):
            logging.info(f"❌ BLOCKED T-BILL / AUCTION: {title}")
            continue

        # 3) Block low-impact ECB/BOE/Fed members
        if is_blocked_speaker(t):
            logging.info(f"❌ BLOCKED MINOR SPEAKER: {title}")
            continue

        # 4) If 'Fed' appears but not Powell → skip
        if "fed" in t and not is_powell(t):
            logging.info(f"⛔ Skipping non-Powell Fed headline: {title}")
            continue

        # 5) Allow Powell always; otherwise require high-impact macro
        if not is_powell(t) and not is_high_macro(t):
            logging.info(f"⚠️ Low Impact Skipped: {title}")
            continue

        logging.info(f"🔥 Approved headline: {title}")

        som = await translate_to_somali(title)
        if not som:
            continue

        tone, horiz, conf = await analyze_sentiment(title)
        msg = f"{som}\n\n({tone} — {horiz} — Confidence: {conf}%)"

        try:
            # Telegram
            await bot.send_message(
                TELEGRAM_CHANNEL_ID,
                msg,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            logging.info("✅ Posted to Telegram.")

            # Facebook
            await post_to_facebook(msg)

        except Exception as err:
            logging.error(f"❌ Telegram/Facebook send error: {err}")

        if e.get("link"):
            save_last_posted_link(e.get("link"))
        if e.get("published_parsed"):
            latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))

        await asyncio.sleep(1)

    save_last_time(latest_timestamp)

###############################################################################
# 8. Main Loop
###############################################################################
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    while True:
        logging.info("♻️ Checking for new headlines...")
        try:
            await fetch_and_post(bot)
        except Exception:
            logging.exception("❌ Fatal error in main loop.")
        logging.info("⏳ Sleeping 60 seconds...\n")
        await asyncio.sleep(60)

if __name__ == "__main__":
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    asyncio.run(main())
