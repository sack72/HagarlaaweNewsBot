# session_analytics.py

from datetime import datetime, time
import sqlite3
from pathlib import Path
from statistics import mean
from collections import defaultdict
from typing import Optional
from openai import OpenAI
import json
import os

# DB path (inside worker container)
DB_PATH = Path("/bot-data/news_sentiment.db")


###############################################################################
# 1. Database Setup
###############################################################################

def _get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            session TEXT NOT NULL,
            currency TEXT NOT NULL,
            sentiment_label TEXT NOT NULL,
            confidence REAL NOT NULL,
            raw_text TEXT NOT NULL
        )
        """
    )
    return conn


###############################################################################
# 2. Session Mapping
###############################################################################

def get_session(dt_utc: datetime) -> str:
    """Map UTC time to trading session."""
    t = dt_utc.time()
    if time(0, 0) <= t < time(8, 0):
        return "Tokyo"
    if time(8, 0) <= t < time(16, 0):
        return "London"
    return "New York"


###############################################################################
# 3. Save Incoming News Item
###############################################################################

def save_news_item(
    currency: str,
    sentiment_label: str,
    confidence: float,
    raw_text: str,
    timestamp_utc: Optional[datetime] = None,
):
    """Save one news item to database."""
    if timestamp_utc is None:
        timestamp_utc = datetime.utcnow()

    session = get_session(timestamp_utc)
    ts_str = timestamp_utc.isoformat()

    conn = _get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO news_items (
                timestamp_utc, session, currency,
                sentiment_label, confidence, raw_text
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts_str, session, currency, sentiment_label, confidence, raw_text),
        )
        conn.commit()
    finally:
        conn.close()


###############################################################################
# 4. Load Today's News
###############################################################################

def load_today_news_items() -> list[dict]:
    """Fetch all saved news for the current UTC day."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT timestamp_utc, session, currency, sentiment_label, confidence, raw_text
            FROM news_items
            """
        ).fetchall()
    finally:
        conn.close()

    items = []
    today = datetime.utcnow().date()

    for ts, session, currency, sentiment, confidence, raw_text in rows:
        dt = datetime.fromisoformat(ts)
        if dt.date() == today:
            items.append(
                {
                    "timestamp": dt,
                    "session": session,
                    "currency": currency,
                    "sentiment_label": sentiment,
                    "confidence": float(confidence),
                    "raw_text": raw_text,
                }
            )
    return items


###############################################################################
# 5. Sentiment Scoring
###############################################################################

def label_to_score(label: str) -> int:
    """Convert Bullish/Bearish/Neutral to numeric."""
    label = label.lower()
    if label == "bullish":
        return 1
    if label == "bearish":
        return -1
    return 0  # neutral


def score_to_label(score: float) -> str:
    """Convert numeric score to label with thresholds."""
    if score > 0.25:
        return "Bullish"
    if score < -0.25:
        return "Bearish"
    return "Neutral"


###############################################################################
# 6. Aggregate Session Sentiment
###############################################################################

def aggregate_session_sentiment():
    """Return aggregated sentiment per session and currency."""
    news_items = load_today_news_items()
    buckets = defaultdict(list)

    for item in news_items:
        key = (item["session"], item["currency"])
        numeric = label_to_score(item["sentiment_label"])
        weighted = numeric * item["confidence"]
        buckets[key].append(weighted)

    result = []

    for (session, currency), scores in buckets.items():
        avg_score = mean(scores)
        label = score_to_label(avg_score)

        result.append(
            {
                "session": session,
                "currency": currency,
                "sentiment_score": round(avg_score, 2),
                "sentiment_label": label,
            }
        )

    return result


###############################################################################
# 7. AI Somali Session Summary
###############################################################################

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def generate_somali_session_summary() -> str:
    """
    Produce a MarketEdge-style Somali dashboard summary.
    """
    data = aggregate_session_sentiment()

    # Prevent crash when no data exists
    if not data:
        return "Ma jiraan xog kulamo maanta oo la falanqeeyo. Sug marka wararka suuqa ay bilaabmaan."

    # Prevent crash if key missing
    if not OPENAI_API_KEY:
        return "API Error: OPENAI_API_KEY lama helin."

    client = OpenAI(api_key=OPENAI_API_KEY)

    system_prompt = (
        "You are Hagarlaawe HMM's professional macro and FX analyst. "
        "Produce a clean, structured Somali session dashboard."
    )

    user_prompt = (
        "Here is today's aggregated session sentiment (JSON):\n"
        f"{json.dumps(data, indent=2)}\n"
        "Write the Somali session dashboard."
    )

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",  "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=400,
    )

    return resp.choices[0].message.content.strip()


###############################################################################
# 8. Save Daily JSON
###############################################################################

JSON_PATH = Path("/bot-data/today_sentiment.json")

def save_daily_json():
    """Save today's aggregated sentiment to JSON."""
    data = aggregate_session_sentiment()
    with open(JSON_PATH, "w") as f:
        json.dump(data, f, indent=2)
