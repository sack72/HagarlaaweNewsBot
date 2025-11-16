# session_analytics.py

from datetime import datetime, time
import sqlite3
from pathlib import Path

DB_PATH = Path("news_sentiment.db")

def _get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            session TEXT NOT NULL,
            currency TEXT NOT NULL,
            sentiment_label TEXT NOT NULL,
            confidence REAL NOT NULL,
            raw_text TEXT NOT NULL
        )
    """)
    return conn

def get_session(dt_utc: datetime) -> str:
    """Map UTC time to trading session."""
    t = dt_utc.time()
    if time(0, 0) <= t < time(8, 0):
        return "Tokyo"
    if time(8, 0) <= t < time(16, 0):
        return "London"
    return "New York"

def save_news_item(
    currency: str,
    sentiment_label: str,
    confidence: float,
    raw_text: str,
    timestamp_utc: datetime | None = None,
):
    """
    Save one news item to the local SQLite database.
    """

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
from statistics import mean
from collections import defaultdict

def label_to_score(label: str) -> int:
    """Convert Bullish/Bearish/Neutral into numeric score."""
    label = label.lower()
    if label == "bullish":
        return 1
    if label == "bearish":
        return -1
    return 0  # Neutral

def score_to_label(score: float) -> str:
    """Convert numeric score back to a label."""
    if score > 0.25:
        return "Bullish"
    if score < -0.25:
        return "Bearish"
    return "Neutral"

def load_today_news_items() -> list[dict]:
    """Fetch all saved news for the current UTC day from the database."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT timestamp_utc, session, currency, sentiment_label, confidence, raw_text
            FROM news_items
        """).fetchall()
    finally:
        conn.close()

    items = []
    today = datetime.utcnow().date()

    for ts, session, currency, sentiment, confidence, raw_text in rows:
        dt = datetime.fromisoformat(ts)
        if dt.date() == today:
            items.append({
                "timestamp": dt,
                "session": session,
                "currency": currency,
                "sentiment_label": sentiment,
                "confidence": float(confidence),
                "raw_text": raw_text,
            })
    return items

def aggregate_session_sentiment():
    """Return aggregated sentiment per session and currency."""
    news_items = load_today_news_items()

    buckets = defaultdict(list)

    for item in news_items:
        key = (item["session"], item["currency"])
        numeric_score = label_to_score(item["sentiment_label"])
        weighted = numeric_score * item["confidence"]
        buckets[key].append(weighted)

    result = []

    for (session, currency), scores in buckets.items():
        avg_score = mean(scores)
        label = score_to_label(avg_score)

        result.append({
            "session": session,
            "currency": currency,
            "sentiment_score": round(avg_score, 2),
            "sentiment_label": label,
        })

    return result
