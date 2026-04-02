"""
main.py - Bot logic only. No Flask here.

Run locally:   python main.py
On Render:     python server.py  ← always use this for deployment

Stability design:
  - BackgroundScheduler (not Blocking) so the main thread can run a keep-alive loop
  - Every job is wrapped in server.safe_run() — exceptions never kill the scheduler
  - Keep-alive loop at the bottom detects a dead scheduler and raises so the
    supervisor in server.py can restart the whole bot cleanly
  - No silent exits
"""

import logging
import sys
import time
import traceback

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

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

log = logging.getLogger(__name__)

# How often the keep-alive loop checks the scheduler health (seconds)
_HEALTH_CHECK_INTERVAL = 30


# ── Config validation ─────────────────────────────────────────────────────────

def _validate_config() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not YOUTUBE_API_KEY:
        missing.append("YOUTUBE_API_KEY")
    if missing:
        log.critical("Missing required env vars: %s — set them in Render environment.", ", ".join(missing))
        sys.exit(1)   # caught by supervisor; won't restart on config error


# ── Safe job wrappers ─────────────────────────────────────────────────────────
# Each wrapper catches all exceptions so APScheduler's thread pool never dies.

def _job_youtube():
    try:
        log.info("[JOB START] youtube")
        check_youtube_channels()
        log.info("[JOB DONE]  youtube")
    except Exception:
        log.error("[JOB ERROR] youtube\n%s", traceback.format_exc())


def _job_twitter():
    try:
        log.info("[JOB START] twitter")
        check_twitter()
        log.info("[JOB DONE]  twitter")
    except Exception:
        log.error("[JOB ERROR] twitter\n%s", traceback.format_exc())


# ── APScheduler event listener ────────────────────────────────────────────────

def _on_job_event(event):
    if event.exception:
        # This is a safety net — individual jobs already catch their own exceptions,
        # but APScheduler itself can raise in edge cases (e.g. misfire handling).
        log.error(
            "[SCHEDULER ERROR] job_id=%s: %s\n%s",
            event.job_id,
            event.exception,
            "".join(traceback.format_tb(event.traceback)),
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("  Notification Bot starting up")
    log.info("=" * 60)

    _validate_config()
    init_db()

    # ── Scheduler setup ───────────────────────────────────────────────────────
    # BackgroundScheduler runs jobs in its own thread pool.
    # The main thread stays free to run the keep-alive / health-check loop below.
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce":          True,    # merge missed runs into one
            "max_instances":     1,       # don't stack concurrent runs of same job
            "misfire_grace_time": 120,
        },
    )
    scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    scheduler.add_job(
        _job_youtube,
        trigger=IntervalTrigger(minutes=YOUTUBE_POLL_INTERVAL_MINUTES),
        id="youtube",
        next_run_time=None,   # don't run at add time; we run manually below
    )

    scheduler.add_job(
        _job_twitter,
        trigger=IntervalTrigger(minutes=TWITTER_POLL_INTERVAL_MINUTES),
        id="twitter",
        next_run_time=None,
    )

    scheduler.start()
    log.info(
        "Scheduler started. YouTube every %d min | Twitter every %d min",
        YOUTUBE_POLL_INTERVAL_MINUTES,
        TWITTER_POLL_INTERVAL_MINUTES,
    )

    # ── Initial checks (run once immediately on startup) ─────────────────────
    log.info("Running initial checks on startup...")
    _job_youtube()
    _job_twitter()

    # ── Keep-alive loop ───────────────────────────────────────────────────────
    # This keeps main() alive (so the supervisor thread in server.py stays happy)
    # and actively monitors the scheduler — if it dies, we raise to trigger a restart.
    log.info("Entering keep-alive loop (health check every %ds)...", _HEALTH_CHECK_INTERVAL)
    try:
        while True:
            time.sleep(_HEALTH_CHECK_INTERVAL)

            if not scheduler.running:
                # Scheduler died — raise so supervisor restarts the whole bot
                raise RuntimeError("APScheduler has stopped unexpectedly.")

            jobs = scheduler.get_jobs()
            log.debug("Heartbeat — scheduler alive, %d job(s) registered.", len(jobs))

    except (KeyboardInterrupt, SystemExit):
        log.info("Shutdown signal received.")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("Scheduler shut down.")


if __name__ == "__main__":
    # Direct local run
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("notifier.log"),
        ],
    )
    main()
