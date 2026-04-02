"""
main.py - Entry point. Starts the scheduler that polls YouTube and Twitter.

Run locally:      python main.py
Run on Render:    python server.py  (Flask keep-alive + this bot)
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
        log.critical("Please set them in your Render environment variables.")
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
        next_run_time=None,
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
        "Scheduler configured. YouTube every %d min | Twitter every %d min",
        YOUTUBE_POLL_INTERVAL_MINUTES,
        TWITTER_POLL_INTERVAL_MINUTES,
    )

    # Run once immediately on startup
    log.info("Running initial checks...")
    check_youtube_channels()
    check_twitter()

    log.info("Scheduler starting...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped by user.")


if __name__ == "__main__":
    main()
