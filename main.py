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

###############################################################################
# 1. Environment & Setup
###############################################################################
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
RSS_URLS_RAW        = os.getenv("RTT_RSS_FEED_URL", "")

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
        except:
            return 0.0
    return 0.0

def save_last_time(timestamp: float) -> None:
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    with open(LAST_TIME_FILE, "w") as f:
        f.write(str(timestamp))

GLOSSARY = {

    # ---------------------------------------------------------
    # CENTRAL BANKS & POLICY
    # ---------------------------------------------------------
    "federal funds rate": "heerka dulsaar ee fed-ka",
    "fed rate": "heerka dulsaar ee fed-ka",
    "ecb": "bangiga dhexe ee yurub",
    "european central bank": "bangiga dhexe ee yurub",
    "boe": "bangiga ingiriiska",
    "bank of england": "bangiga ingiriiska",
    "boj": "bangiga japan",
    "bank of japan": "bangiga japan",
    "monetary policy": "siyaasadda lacagta",
    "policy tightening": "adkeynta siyaasadda lacagta",
    "policy easing": "fududeynta siyaasadda lacagta",
    "interest rate decision": "go’aanka heerka dulsaar",
    "forward guidance": "tilmaanta siyaasadda mustaqbalka",
    "quantitative easing": "kordhinta lacagta wareegaysa (QE)",
    "qe": "kordhinta lacagta wareegaysa (QE)",
    "balance sheet reduction": "yaraynta miisaaniyadda (QT)",
    "balance sheet expansion": "ballaarinta miisaaniyadda",
    "liquidity injection": "gelinta lacagta dareeraha ah",
    "overnight rate": "heerka dulsaar ee habeenka",
    "dot plot": "jadwalka saadaasha fed-ka",
    "fomc": "guddiga suuqa furan ee fed-ka",

    # ---------------------------------------------------------
    # MACRO DATA: OUTPUT, SURVEYS & HOUSING
    # ---------------------------------------------------------
    "gdp": "wax-soo-saarka guud ee dalka (GDP)",
    "gdp growth rate": "heerka kobaca gdp-ga",
    "industrial production": "wax-soo-saarka warshadaha",
    "pmi": "tusmada maareeyayaasha iibsiga (PMI)",
    "ism": "tusmada ISM ee warshadaha/adeegyada",
    "ism manufacturing": "tusmada ism ee warshadaha",
    "ism non-manufacturing": "tusmada ism ee adeegyada",
    "durable goods orders": "amarada alaabta waara",
    "business confidence": "tusmada kalsoonida ganacsiga",
    "new home sales": "iibka guryaha cusub",
    "housing starts": "dhismaha guryaha la bilaabay",
    "building permits": "ogolaanshaha dhismaha",

    # ---------------------------------------------------------
    # INFLATION & PRICE INDEXES
    # ---------------------------------------------------------
    "inflation": "sicir-bararka",
    "inflation rate": "heerka sicir-bararka",
    "deflation": "sicir-hoos-u-dhac",
    "cpi": "tusmada qiimaha macaamiisha (CPI)",
    "consumer price index": "tusmada qiimaha macaamiisha (CPI)",
    "core cpi": "cpi-ga asaaska ah",
    "ppi": "tusmada qiimaha soo-saareyaasha (PPI)",
    "producer price index": "tusmada qiimaha soo-saareyaasha (PPI)",
    "pce": "tusmada kharashaadka macaamiisha (PCE)",
    "core pce": "pce-ga asaaska ah",

    # ---------------------------------------------------------
    # LABOR MARKET & WAGES
    # ---------------------------------------------------------
    "nfp": "shaqooyinka aan beeraha ahayn (NFP)",
    "non-farm payrolls": "shaqooyinka aan beeraha ahayn (NFP)",
    "unemployment rate": "heerka shaqo-la’aanta",
    "initial jobless claims": "codsiyada shaqo-la’aanta ee ugu horreeya",
    "jobless claims": "codsiyada shaqo-la’aanta",
    "average hourly earnings": "dakhliga celceliska saacaddiiba",
    "labor force participation rate": "heerka ka-qaybgalka shaqaalaha",

    # ---------------------------------------------------------
    # SPENDING, INCOME & SENTIMENT
    # ---------------------------------------------------------
    "retail sales": "iibka tafaariiqda",
    "consumer confidence": "tusmada kalsoonida macaamiisha",
    "consumer sentiment": "tusmada dareenka macaamiisha",
    "consumer confidence index": "tusmada kalsoonida macaamiisha (CCI)",
    "personal income": "dakhliga shakhsiya",
    "personal spending": "kharashaadka shakhsiya",

    # ---------------------------------------------------------
    # TRADE & INTERNATIONAL FINANCE
    # ---------------------------------------------------------
    "trade balance": "dheellitirka ganacsiga",
    "balance of trade": "dheellitirka ganacsiga",
    "current account": "koontada hadda",
    "capital flows": "dhaqdhaqaaqa caasimadda",
    "forex reserves": "kaydka lacagaha caalamiga ah",
    "wti crude oil": "saliidda WTI",
    "brent crude oil": "saliidda Brent",

    # ---------------------------------------------------------
    # BONDS, YIELDS & FIXED INCOME
    # ---------------------------------------------------------
    "treasury": "treasury-ga mareykanka",
    "bond yields": "wax-soo-saarka bonds-ka",
    "yield": "wax-soo-saarka bonds-ka",
    "treasury yield": "yield-ka treasury-ga",
    "10-year treasury yield": "yield-ka treasury-ga ee 10-sano",
    "yield curve": "qalooca wax-soo-saarka",
    "yield curve inversion": "rogmadka qalooca wax-soo-saarka",
    "credit spread": "farqiga deymaha",
    "corporate bonds": "bonds-ka shirkadaha",
    "junk bonds": "bonds-ka halista badan",
    "sovereign debt": "deynta dowladdu leedahay",
    "auction demand": "baahida xaraashka",

    # ---------------------------------------------------------
    # MARKET SENTIMENT & VOLATILITY
    # ---------------------------------------------------------
    "market sentiment": "dareenka suuqa",
    "risk sentiment": "jihada khatarta suuqa",
    "volatility": "kacsanaanta suuqa",
    "low volatility": "suuq deggan",
    "high volatility": "suuq kacsan",
    "selloff": "iib-sii kordhay (selloff)",
    "rally": "kordh kac suuqa (rally)",
    "correction": "dib-u-habeyn suuqa",
    "bear market": "suuq hoos u socda (Bear)",
    "bull market": "suuq kor u socda (Bull)",
    "vix": "tusmada halisaha (VIX)",
    "risk-off": "jaho khatar ka fogaansho (Risk-Off)",
    "risk-on": "jaho khatar qaadasho (Risk-On)",
    "basis point": "barta qiimeed (bp)",
    "bp": "barta qiimeed (bp)",

    # ---------------------------------------------------------
    # EQUITIES & INDEXES
    # ---------------------------------------------------------
    "equity index": "tusmada saamiyada",
    "stock index": "tusmada saamiyada",
    "s&p 500": "tusmada saamiyada S&P 500",
    "nasdaq": "suuqa saamiyada Nasdaq",
    "dow jones": "tusmada Dow Jones",

    # ---------------------------------------------------------
    # FOREX MARKETS (FX)
    # ---------------------------------------------------------
    "usd": "doollar mareykanka",
    "eur": "yuuro",
    "jpy": "yen-ka japan",
    "gbp": "gini ingiriis",
    "chf": "faransiiska swiss-ka",
    "cad": "doollar kanada",
    "aud": "doollar australiya",
    "nzd": "doollar new zealand",
    "safe haven currency": "lacagta badbaadada lagu aado",
    "currency depreciation": "hoos u dhaca qiimaha lacagta",
    "currency appreciation": "kor u kaca qiimaha lacagta",
    "fx intervention": "faragelinta suuqyada lacagaha",
    "exchange rate": "heerka is-weydaarsiga",
    "dollar strength": "awoodda doollarka",
    "dollar weakness": "doollar daciif ah",
    "carry trade": "ganacsiga dulsaar-ka-faa'iidada",

    # ---------------------------------------------------------
    # COMMODITIES & ENERGY
    # ---------------------------------------------------------
    "gold": "dahab",
    "spot gold": "dahabka suuqa joogta ah",
    "gold futures": "mustaqbalka dahabka",
    "oil": "saliid",
    "crude oil": "saliid cayriin",
    "natural gas": "shidaalka dabiiciga ah",
    "energy market": "suuqa tamarta",
    "opec": "ururka dalalka saliidda",
    "opec+": "opec+",
    "oil output": "wax-soo-saarka saliidda",
    "supply disruption": "carqalad ku timid sahayda",

    # ---------------------------------------------------------
    # DERIVATIVES & INSTRUMENTS
    # ---------------------------------------------------------
    "futures": "qandaraasyada mustaqbalka",
    "options": "ikhtiyaarada ganacsiga (Options)",
    "options contracts": "ikhtiyaarada ganacsiga",
    "derivatives": "waxyaabaha laga soo farcamay suuqyada",
    "leverage": "awood-dheereysi (leverage)",
    "margin": "dhigaal (margin)",
    "etf": "sanduuqa saamiyada la ganacsado (ETF)",
    "index fund": "sanduuqa tusmada saamiyada",
}
def apply_glossary(text: str) -> str:
    for eng, som in GLOSSARY.items():
        pattern = re.compile(r"\b" + re.escape(eng) + r"\b", re.IGNORECASE)
        text = pattern.sub(som, text)
    return text

async def translate_to_somali(text: str) -> str:
    try:
        logging.info(f"Translating: {text}")
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            resp = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "Translate this into clear Somali financial-news style."},
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
            )
            return apply_glossary(resp.choices[0].message.content.strip())
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        return ""

###############################################################################
# 4. Sentiment
###############################################################################
async def analyze_sentiment(text: str):
    try:
        async with httpx.AsyncClient() as http_client:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
            resp = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content":
                     "Return Tone (Bullish/Bearish/Neutral), Horizon, Confidence %."},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=25,
            )
            out = resp.choices[0].message.content.strip()
    except:
        return ("Neutral", "Unknown", 50)

    tone  = re.search(r"(Bullish|Bearish|Neutral)", out, re.IGNORECASE)
    horiz = re.search(r"(Intraday|Short-term|Medium-term|Macro)", out, re.IGNORECASE)
    conf  = re.search(r"Confidence:\s*(\d+)", out)

    tone  = tone.group(1).capitalize() if tone else "Neutral"
    horiz = horiz.group(1).capitalize() if horiz else "Unknown"
    conf  = int(conf.group(1)) if conf else 50

    return (tone, horiz, max(0, min(conf, 100)))

###############################################################################
# 5. High Impact Filtering (Powell only + Macro only)
###############################################################################

# Tier-1 allowed voices: Powell only
POWELL_ONLY = [
    "jerome powell",
    "powell",
    "fed chair powell",
]

# Block low-impact ECB/BOE/Fed members
BLOCKED_SPEAKERS = [
    "de guindos", "makhlouf", "sleijpen", "sliejpen", "merz",
    "mann", "wells", "he lifeng",
    "jefferson", "harker", "barkin", "goolsbee", "mester",
    "logan", "cook", "daly", "collins", "barr", "kashkari",
]

# High macro data
HIGH_IMPACT_MACRO = [
    "cpi", "inflation", "pce", "core pce", "core cpi",
    "nfp", "nonfarm", "unemployment", "jobless claims",
    "gdp", "retail sales", "ppi",
    "pmi", "ism",
    "yield", "yields", "treasury", "risk-off", "risk-on",
    "fomc", "fed policy", "federal reserve",
    "market crash", "selloff", "volatility",
    "white house", "biden", "madaxweyne donald trump",
]

def contains(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords)

def is_powell(text: str) -> bool:
    return contains(text, POWELL_ONLY)

def is_blocked(text: str) -> bool:
    return contains(text, BLOCKED_SPEAKERS)

def is_high_macro(text: str) -> bool:
    return contains(text, HIGH_IMPACT_MACRO)

###############################################################################
# 6. Posting Loop
###############################################################################
async def fetch_and_post(bot: Bot):
    last_link = load_last_posted_link()
    last_time = load_last_time()
    new_items: list[Any] = []

    for url in RSS_URLS:
        logging.info(f"Fetching feed: {url}")
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
        logging.info("No new items.")
        return

    latest_timestamp = last_time

    for e in new_items:
        title = e.title or ""
        t = title.lower()

        # ------------------------
        # MASTER FILTER SYSTEM
        # ------------------------
        if is_blocked(t):
            logging.info(f"❌ BLOCKED NOISE: {title}")
            continue

        if "fed" in t and not is_powell(t):
            logging.info(f"⛔ Skipping non-Powell Fed: {title}")
            continue

        if not is_powell(t) and not is_high_macro(t):
            logging.info(f"⚠️ Low Impact Skipped: {title}")
            continue

        logging.info(f"🔥 Approved headline: {title}")

        # Clean/translate/post
        som = await translate_to_somali(title)
        tone, horiz, conf = await analyze_sentiment(title)

        msg = f"{som}\n\n({tone} — {horiz} — Confidence: {conf}%)"

        try:
            await bot.send_message(
                TELEGRAM_CHANNEL_ID,
                msg,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            logging.info("Posted to Telegram.")
        except Exception as err:
            logging.error(f"❌ Telegram error: {err}")

        if e.get("link"):
            save_last_posted_link(e.get("link"))
        if e.get("published_parsed"):
            latest_timestamp = max(latest_timestamp, time.mktime(e.get("published_parsed")))

        await asyncio.sleep(1)

    save_last_time(latest_timestamp)

###############################################################################
# 7. Main Loop
###############################################################################
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    while True:
        logging.info("♻️ Checking for new headlines...")
        try:
            await fetch_and_post(bot)
        except Exception:
            logging.exception("Fatal error in main loop.")

        logging.info("Sleeping 60 seconds...\n")
        await asyncio.sleep(60)

if __name__ == "__main__":
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    asyncio.run(main())
