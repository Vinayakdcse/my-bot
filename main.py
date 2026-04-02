"""
main.py - Bot logic only.

Run locally:   python main.py
On Render:     python server.py

ROOT CAUSES FIXED:
  1. yt-dlp download during initial checks was blocking the thread for
     minutes per video × 7 channels = potential 20-40 min startup block.
     During this time Render's health checks timeout → container killed.
     FIX: yt-dlp downloads are DISABLED on startup initial checks.
          Only new videos found by the scheduler get download attempts.

  2. ThreadPoolExecutor with max_workers=10 and 7 channels × concurrent
     yt-dlp downloads = 7 threads each consuming 200-400 MB RAM.
     Render free tier = 512 MB total. OOM kill → silent container restart.
     FIX: yt-dlp downloads are sequential (not in thread pool),
          and max_workers capped at 4.

  3. The keep-alive loop raised RuntimeError if scheduler stopped, which
     propagated as an exception to the supervisor. But this also happened
     during clean shutdowns. FIX: distinguish clean vs unexpected stop.

  4. update_heartbeat injected from server.py so both files stay decoupled.
"""

import logging
import sys
import time
import traceback

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR

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

# Injected by server.py before calling main() so heartbeat works.
# Falls back to a no-op if run standalone (python main.py).
def update_heartbeat(**kwargs):
    pass   # replaced by server.py at runtime

_HEALTH_INTERVAL   = 30     # seconds between keep-alive ticks
_shutdown_requested = False


# ── Config validation ─────────────────────────────────────────────────────────

def _validate_config() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN:  missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:    missing.append("TELEGRAM_CHAT_ID")
    if not YOUTUBE_API_KEY:     missing.append("YOUTUBE_API_KEY")
    if missing:
        log.critical("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)


# ── Safe job wrappers ─────────────────────────────────────────────────────────

def _job_youtube():
    try:
        log.info("[JOB START] youtube")
        check_youtube_channels()
        log.info("[JOB DONE]  youtube")
        update_heartbeat(running=True)
    except Exception:
        log.error("[JOB ERROR] youtube\n%s", traceback.format_exc())


def _job_twitter():
    try:
        log.info("[JOB START] twitter")
        check_twitter()
        log.info("[JOB DONE]  twitter")
        update_heartbeat(running=True)
    except Exception:
        log.error("[JOB ERROR] twitter\n%s", traceback.format_exc())


# ── Scheduler event listener ──────────────────────────────────────────────────

def _on_job_error(event):
    log.error(
        "[SCHEDULER ERROR] job=%s exc=%s\n%s",
        event.job_id,
        event.exception,
        "".join(traceback.format_tb(event.traceback)) if event.traceback else "",
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global _shutdown_requested
    _shutdown_requested = False

    log.info("=" * 60)
    log.info("  Notification Bot starting up")
    log.info("=" * 60)

    _validate_config()
    init_db()

    # ── Scheduler ────────────────────────────────────────────────────────────
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce":           True,   # merge missed runs
            "max_instances":      1,      # no stacking
            "misfire_grace_time": 120,
        },
    )
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    scheduler.add_job(
        _job_youtube,
        trigger=IntervalTrigger(minutes=YOUTUBE_POLL_INTERVAL_MINUTES),
        id="youtube",
        next_run_time=None,
    )
    scheduler.add_job(
        _job_twitter,
        trigger=IntervalTrigger(minutes=TWITTER_POLL_INTERVAL_MINUTES),
        id="twitter",
        next_run_time=None,
    )

    scheduler.start()
    log.info(
        "Scheduler started — YouTube every %d min | Twitter every %d min",
        YOUTUBE_POLL_INTERVAL_MINUTES,
        TWITTER_POLL_INTERVAL_MINUTES,
    )

    # ── Startup checks ────────────────────────────────────────────────────────
    # Run jobs immediately but with a short stagger so they don't all hit
    # the network simultaneously and OOM the container.
    log.info("Running startup checks...")
    _job_twitter()          # Twitter first (lighter)
    time.sleep(3)
    _job_youtube()          # YouTube second (heavier)

    # ── Keep-alive loop ───────────────────────────────────────────────────────
    log.info("Entering keep-alive loop...")
    try:
        while True:
            time.sleep(_HEALTH_INTERVAL)

            if not scheduler.running:
                raise RuntimeError("APScheduler stopped unexpectedly.")

            update_heartbeat(running=True)
            log.debug("Heartbeat — scheduler alive, jobs: %d", len(scheduler.get_jobs()))

    except (KeyboardInterrupt, SystemExit):
        _shutdown_requested = True
        log.info("Shutdown signal received.")

    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("Scheduler stopped.")

    # Only re-raise if this was an unexpected exit (lets supervisor restart)
    if not _shutdown_requested:
        raise RuntimeError("main() exited without shutdown signal.")


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("notifier.log"),
        ],
    )
    main()
