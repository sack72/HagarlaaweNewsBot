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
