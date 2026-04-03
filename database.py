"""
database.py - Thread-safe SQLite with WAL mode.

FIX: DATABASE_PATH defaults to "seen_ids.db" (relative path).
     On Render, the working directory is /app but after a container restart
     the /app filesystem may be reset (ephemeral storage), losing the DB.
     The env var DATABASE_PATH should point to a persistent volume path
     e.g. /app/data/seen_ids.db (set in Render environment variables).

     If no persistent volume: the DB resets on each deploy and the bot
     re-sends all historical content. This is NOT a code bug — it's a
     Render free-tier limitation. Use Render's Disk add-on or an external
     SQLite host (Turso, Railway volume) for persistence.
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
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=10000")   # FIX: wait up to 10s on lock instead of failing
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
    log.info("Database ready: %s", DATABASE_PATH)


def is_video_seen(video_id: str) -> bool:
    try:
        with _conn() as con:
            return con.execute(
                "SELECT 1 FROM seen_youtube WHERE video_id=?", (video_id,)
            ).fetchone() is not None
    except Exception as exc:
        log.error("is_video_seen(%s): %s", video_id, exc)
        return False


def mark_video_seen(video_id: str, channel_id: str) -> None:
    try:
        with _conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO seen_youtube (video_id, channel_id) VALUES (?,?)",
                (video_id, channel_id),
            )
    except Exception as exc:
        log.error("mark_video_seen(%s): %s", video_id, exc)


def is_tweet_seen(tweet_id: str) -> bool:
    try:
        with _conn() as con:
            return con.execute(
                "SELECT 1 FROM seen_tweets WHERE tweet_id=?", (tweet_id,)
            ).fetchone() is not None
    except Exception as exc:
        log.error("is_tweet_seen(%s): %s", tweet_id, exc)
        return False


def mark_tweet_seen(tweet_id: str) -> None:
    try:
        with _conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO seen_tweets (tweet_id) VALUES (?)",
                (tweet_id,),
            )
    except Exception as exc:
        log.error("mark_tweet_seen(%s): %s", tweet_id, exc)
