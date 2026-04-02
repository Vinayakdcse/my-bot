"""
main.py - Entry point. Starts the scheduler that polls YouTube and Twitter.

Run locally:      python main.py
Run as service:   see README.md → Deployment section
"""

import logging
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    YOUTUBE_API_KEY,
    YOUTUBE_POLL_INTERVAL_MINUTES,
    TWITTER_POLL_INTERVAL_MINUTES,
    LOG_LEVEL,
)
from database import init_db
from youtube_checker import check_youtube_channels
from twitter_checker import check_twitter

# ── Logging ──────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("notifier.log"),
    ],
)
log = logging.getLogger(__name__)


# ── Startup checks ───────────────────────────

def _validate_config() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not YOUTUBE_API_KEY:
        missing.append("YOUTUBE_API_KEY")
    if missing:
        log.critical("Missing required env vars: %s", ", ".join(missing))
        log.critical("Please fill in your .env file and restart.")
        sys.exit(1)


# ── Main ─────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("  Notification Bot starting up")
    log.info("=" * 60)

    _validate_config()
    init_db()

    scheduler = BlockingScheduler(timezone="UTC")

    # YouTube — every N minutes
    scheduler.add_job(
        check_youtube_channels,
        trigger=IntervalTrigger(minutes=YOUTUBE_POLL_INTERVAL_MINUTES),
        id="youtube",
        next_run_time=None,   # don't run immediately on start (avoids spam on first boot)
        misfire_grace_time=120,
    )

    # Twitter — every N minutes
    scheduler.add_job(
        check_twitter,
        trigger=IntervalTrigger(minutes=TWITTER_POLL_INTERVAL_MINUTES),
        id="twitter",
        next_run_time=None,
        misfire_grace_time=60,
    )

    log.info(
        "Scheduler started. YouTube every %d min | Twitter every %d min",
        YOUTUBE_POLL_INTERVAL_MINUTES,
        TWITTER_POLL_INTERVAL_MINUTES,
    )

    # Run once immediately on startup (after a short delay so logs settle)
    time.sleep(3)
    log.info("Running initial checks...")
    check_youtube_channels()
    check_twitter()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped by user.")


if __name__ == "__main__":
    main()

from flask import Flask
import threading
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running ✅"


def run_bot():
    main()


if __name__ == "__main__":
    # Run bot in background
    threading.Thread(target=run_bot).start()

    # Run web server (main thread)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)