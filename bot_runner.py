"""
bot_runner.py  ← NEW FILE (replaces main.py as the runnable unit)

Why a new file instead of reloading main.py?
  importlib.reload() is unreliable for anything with side effects:
    - It re-runs module-level code in main.py only
    - Sub-modules (config, database, checkers) keep their OLD state
    - BackgroundScheduler threads from the previous run keep running
    - Result: two schedulers race, DB connections in undefined state

  This file creates ALL state inside run() — nothing at module level.
  server.py imports this and calls run() fresh each restart.
  Previous run's objects go out of scope and are garbage-collected cleanly.

Run locally:  python bot_runner.py
On Render:    python server.py  (calls run() via supervisor)
"""

import logging
import sys
import time
import traceback

log = logging.getLogger(__name__)


def run(heartbeat_fn=None) -> None:
    """
    Full bot lifecycle — setup, scheduler, keep-alive loop.
    Returns cleanly on KeyboardInterrupt/SIGTERM.
    Raises on unexpected failures so supervisor can restart.
    """

    # ── All imports inside run() ──────────────────────────────────────────────
    # This guarantees fresh module state on every call from the supervisor.
    # Python re-uses cached modules (sys.modules) but since ALL mutable state
    # (scheduler, DB connections, HTTP clients) is created below — not at module
    # level — there is no stale state to worry about.

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.events import EVENT_JOB_ERROR

    from config import (
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, YOUTUBE_API_KEY,
        YOUTUBE_POLL_INTERVAL_MINUTES, TWITTER_POLL_INTERVAL_MINUTES, LOG_LEVEL,
    )
    from database import init_db
    from youtube_checker import check_youtube_channels
    from twitter_checker import check_twitter

    # ── Config validation ─────────────────────────────────────────────────────
    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT_ID,
        "YOUTUBE_API_KEY":    YOUTUBE_API_KEY,
    }.items() if not v]

    if missing:
        log.critical("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    # ── Database ──────────────────────────────────────────────────────────────
    init_db()

    # ── Safe job wrappers ─────────────────────────────────────────────────────
    # CRITICAL: exceptions inside a job must NEVER propagate out.
    # If they do, APScheduler marks the job as failed and depending on version
    # may stop rescheduling it. Catch everything, log, return normally.

    def _job_youtube():
        try:
            log.info("[JOB START] youtube")
            check_youtube_channels()
            log.info("[JOB DONE]  youtube")
            if heartbeat_fn:
                heartbeat_fn()
        except Exception:
            log.error("[JOB ERROR] youtube\n%s", traceback.format_exc())

    def _job_twitter():
        try:
            log.info("[JOB START] twitter")
            check_twitter()
            log.info("[JOB DONE]  twitter")
            if heartbeat_fn:
                heartbeat_fn()
        except Exception:
            log.error("[JOB ERROR] twitter\n%s", traceback.format_exc())

    def _on_scheduler_error(event):
        # Belt-and-suspenders: catches errors APScheduler itself raises
        # (e.g. during misfire handling) that bypass job-level try/except
        log.error(
            "[SCHEDULER] job=%s crashed: %s\n%s",
            event.job_id,
            event.exception,
            "".join(traceback.format_tb(event.traceback)) if event.traceback else "",
        )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # FIX: executor='threadpool' with max_workers=2 (one per job type).
    # Default threadpool is unbounded — jobs pile up when slow, exhaust RAM.
    from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPool

    scheduler = BackgroundScheduler(
        timezone="UTC",
        executors={"default": APSThreadPool(max_workers=2)},
        job_defaults={
            "coalesce":           True,    # skip missed runs, don't pile up
            "max_instances":      1,       # never run same job twice at once
            "misfire_grace_time": 60,
        },
    )
    scheduler.add_listener(_on_scheduler_error, EVENT_JOB_ERROR)

    scheduler.add_job(
        _job_youtube,
        trigger=IntervalTrigger(minutes=YOUTUBE_POLL_INTERVAL_MINUTES),
        id="youtube",
        next_run_time=None,   # don't fire immediately on add — we call manually below
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
    # Run once immediately so we don't wait a full interval on first boot.
    # Stagger by 5s so they don't hit the network simultaneously.
    log.info("Running startup checks...")
    _job_twitter()
    time.sleep(5)
    _job_youtube()

    # ── Keep-alive loop ───────────────────────────────────────────────────────
    # This is the main loop. It does three things:
    #   1. Keeps this thread (and therefore the process) alive
    #   2. Checks that the scheduler is still running every 30s
    #   3. Updates the heartbeat so the watchdog in server.py knows we're alive
    #
    # If scheduler.running becomes False, we raise — supervisor will restart.
    log.info("Bot running. Keep-alive loop active.")

    shutdown_clean = False
    try:
        while True:
            time.sleep(30)

            if not scheduler.running:
                raise RuntimeError("APScheduler stopped unexpectedly — restarting bot.")

            if heartbeat_fn:
                heartbeat_fn()
            log.debug(
                "Heartbeat OK — scheduler alive, %d job(s)",
                len(scheduler.get_jobs()),
            )

    except (KeyboardInterrupt, SystemExit):
        shutdown_clean = True
        log.info("Shutdown signal received.")

    finally:
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
                log.info("Scheduler shut down.")
        except Exception:
            pass   # don't mask the real exception

    if not shutdown_clean:
        # Unexpected exit — supervisor should restart
        raise RuntimeError("run() exited unexpectedly.")


# ── Local run support ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import logging as _logging
    from config import LOG_LEVEL
    _logging.basicConfig(
        level=getattr(_logging, LOG_LEVEL, _logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            _logging.StreamHandler(sys.stdout),
            _logging.FileHandler("notifier.log"),
        ],
    )
    run()
