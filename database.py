"""
database.py - Thread-safe SQLite helper using WAL mode + per-call connections.

FIX: sqlite3 connections are NOT thread-safe when shared.
     We use check_same_thread=False + WAL journal mode so multiple
     threads can read/write without "database is locked" errors.
"""

import os
import sqlite3
import logging
from config import DATABASE_PATH

_db_dir = os.path.dirname(DATABASE_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

log = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=15)
    # WAL mode: readers don't block writers and vice-versa
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen_youtube (
                video_id   TEXT PRIMARY KEY,
                channel_id TEXT,
                seen_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen_tweets (
                tweet_id TEXT PRIMARY KEY,
                seen_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    log.info("Database initialised at %s", DATABASE_PATH)


# ── YouTube ───────────────────────────────────────────────────────────────────

def is_video_seen(video_id: str) -> bool:
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT 1 FROM seen_youtube WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row is not None
    except Exception as exc:
        log.error("is_video_seen error: %s", exc)
        return False   # safe default: treat as unseen so we don't skip


def mark_video_seen(video_id: str, channel_id: str) -> None:
    try:
        with _conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO seen_youtube (video_id, channel_id) VALUES (?, ?)",
                (video_id, channel_id),
            )
        log.debug("Marked video seen: %s", video_id)
    except Exception as exc:
        log.error("mark_video_seen error: %s", exc)


# ── Twitter ───────────────────────────────────────────────────────────────────

def is_tweet_seen(tweet_id: str) -> bool:
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT 1 FROM seen_tweets WHERE tweet_id = ?", (tweet_id,)
            ).fetchone()
        return row is not None
    except Exception as exc:
        log.error("is_tweet_seen error: %s", exc)
        return False


def mark_tweet_seen(tweet_id: str) -> None:
    try:
        with _conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO seen_tweets (tweet_id) VALUES (?)",
                (tweet_id,),
            )
        log.debug("Marked tweet seen: %s", tweet_id)
    except Exception as exc:
        log.error("mark_tweet_seen error: %s", exc)
