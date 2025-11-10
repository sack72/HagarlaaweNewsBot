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
from typing import Optional, List, Dict, Any

###############################################################################
# 1. Environment & Setup
###############################################################################
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
TELEGRAM_CRYPTO_CHANNEL_ID = os.getenv("TELEGRAM_CRYPTO_CHANNEL_ID")
RSS_URLS_RAW        = os.getenv("RTT_RSS_FEED_URL", "")
CRYPTO_RSS_FEEDS_RAW = os.getenv("CRYPTO_RSS_FEEDS", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENAI_API_KEY]):
    logging.error("Missing required environment variables.")
    sys.exit(1)

RSS_URLS = [u.strip() for u in RSS_URLS_RAW.split(",") if u.strip()]
CRYPTO_RSS_FEEDS = [u.strip() for u in CRYPTO_RSS_FEEDS_RAW.split(",") if u.strip()]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

###############################################################################
# 2. Persistent Storage
###############################################################################
PERSISTENT_STORAGE_PATH = "/bot-data"
BRAND_IMAGE_PATH = os.path.join(PERSISTENT_STORAGE_PATH, "hmm_brand.jpg")
LAST_LINK_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_posted_link.txt")
LAST_PUBLISHED_TIME_FILE = os.path.join(PERSISTENT_STORAGE_PATH, "last_published_time.txt")

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

def load_last_published_time() -> float:
    if os.path.isfile(LAST_PUBLISHED_TIME_FILE):
        try:
            with open(LAST_PUBLISHED_TIME_FILE, "r") as f:
                return float(f.read().strip())
        except:
            return 0.0
    return 0.0

def save_last_published_time(timestamp: float) -> None:
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_PUBLISHED_TIME_FILE, "w") as f:
        f.write(str(timestamp))

###############################################################################
# 3. Translation + Glossary
###############################################################################
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
        logging.info(f"📝 Translating: {text}")
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

            response = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "Translate this financial or crypto news into clear, professional Somali."},
                    {"role": "user", "content": text}
                ],
                temperature=0.3,
                max_tokens=300,
            )
            somali = response.choices[0].message.content.strip()
            return apply_glossary(somali)
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        return ""

###############################################################################
# 4. Sentiment Analyzer
###############################################################################
async def analyze_sentiment(text: str) -> tuple[str, str, int]:
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            resp = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": (
                        "You are a financial analyst. Determine Tone (Bullish, Bearish, Neutral), "
                        "Horizon (Intraday, Short-term, Medium-term, Macro), and Confidence (0–100). "
                        "Return exactly this format:\nTone: X; Horizon: Y; Confidence: Z"
                    )},
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=25,
            )
            out = resp.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Sentiment failed: {e}")
        return ("Neutral", "Unknown", 50)

    tone = re.search(r"(Bullish|Bearish|Neutral)", out, re.I)
    horizon = re.search(r"(Intraday|Short-term|Medium-term|Macro)", out, re.I)
    conf = re.search(r"Confidence:\s*(\d+)", out)

    return (
        tone.group(1).capitalize() if tone else "Neutral",
        horizon.group(1).capitalize() if horizon else "Unknown",
        int(conf.group(1)) if conf else 50
    )

###############################################################################
# 5. Facebook Posting (for main bot only)
###############################################################################
async def post_to_facebook(message: str) -> None:
    page_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("FACEBOOK_PAGE_ID")
    if not page_token or not page_id:
        return
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
# 6. Forex + Macro Feed
###############################################################################
async def fetch_and_post_headlines(bot: Bot):
    last_link = load_last_posted_link()
    last_time = load_last_published_time()
    new_items = []

    for url in RSS_URLS:
        logging.info(f"🔄 Fetching feed: {url}")
        feed = feedparser.parse(url)
        for e in feed.entries:
            if e.link == last_link:
                break
            if e.get("published_parsed") and time.mktime(e.published_parsed) <= last_time:
                continue
            new_items.append(e)

    new_items.sort(key=lambda x: x.get("published_parsed") or time.gmtime())
    if not new_items:
        logging.info("📭 No new macro/forex items.")
        return

    for e in new_items:
        title = e.title
        somali = await translate_to_somali(title)
        tone, horizon, conf = await analyze_sentiment(title)
        msg = f"💵 {somali}\n\n({tone} — {horizon} — Confidence: {conf}%)"
        logging.info(f"📤 Sending: {msg[:80]}")

        try:
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="Markdown")
            await post_to_facebook(msg)
            save_last_posted_link(e.link)
        except Exception as ex:
            logging.error(f"Send failed: {ex}")
        await asyncio.sleep(1)

###############################################################################
# 7. Crypto Feed
###############################################################################
async def fetch_and_post_crypto(bot: Bot):
    if not TELEGRAM_CRYPTO_CHANNEL_ID or not CRYPTO_RSS_FEEDS:
        logging.info("⚠️ No crypto channel or feeds configured.")
        return

    for url in CRYPTO_RSS_FEEDS:
        logging.info(f"🔄 Fetching crypto feed: {url}")
        feed = feedparser.parse(url)
        for e in feed.entries[:10]:
            title = e.title
            if not any(k.lower() in title.lower() for k in ["bitcoin", "ethereum", "crypto", "blockchain", "bnb", "solana", "xrp"]):
                continue
            somali = await translate_to_somali(title)
            tone, horizon, conf = await analyze_sentiment(title)
            msg = f"🪙 {somali}\n\n({tone} — {horizon} — Confidence: {conf}%)"
            try:
                await bot.send_message(chat_id=TELEGRAM_CRYPTO_CHANNEL_ID, text=msg, parse_mode="Markdown")
                logging.info(f"✅ Posted crypto: {title[:60]}...")
            except Exception as e:
                logging.error(f"❌ Crypto send failed: {e}")
            await asyncio.sleep(2)

###############################################################################
# 8. Main Runner
###############################################################################
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    while True:
        try:
            await fetch_and_post_headlines(bot)
            await fetch_and_post_crypto(bot)
        except Exception as e:
            logging.exception("Main loop error.")
        logging.info("⏳ Sleeping 60 seconds...\n")
        await asyncio.sleep(60)

if __name__ == "__main__":
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    asyncio.run(main())
