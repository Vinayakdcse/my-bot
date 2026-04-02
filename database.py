"""
database.py - SQLite helper to persist already-sent video/tweet IDs.
Prevents duplicate Telegram notifications across restarts.
"""

import os
import sqlite3
import logging
from config import DATABASE_PATH

# Ensure the directory exists (useful when DATABASE_PATH is an abs path like /app/data/...)
os.makedirs(os.path.dirname(DATABASE_PATH) if os.path.dirname(DATABASE_PATH) else ".", exist_ok=True)

log = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DATABASE_PATH)


def init_db() -> None:
    """Create tables if they don't exist yet."""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen_youtube (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT,
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen_tweets (
                tweet_id TEXT PRIMARY KEY,
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    log.debug("Database initialised at %s", DATABASE_PATH)


# ── YouTube ──────────────────────────────────

def is_video_seen(video_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM seen_youtube WHERE video_id = ?", (video_id,)
        ).fetchone()
    return row is not None


def mark_video_seen(video_id: str, channel_id: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO seen_youtube (video_id, channel_id) VALUES (?, ?)",
            (video_id, channel_id),
        )
    log.debug("Marked YouTube video as seen: %s", video_id)


# ── Twitter ──────────────────────────────────

def is_tweet_seen(tweet_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM seen_tweets WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
    return row is not None


def mark_tweet_seen(tweet_id: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO seen_tweets (tweet_id) VALUES (?)",
            (tweet_id,),
        )
    log.debug("Marked tweet as seen: %s", tweet_id)
