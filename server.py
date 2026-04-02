"""
server.py - Render deployment entry point.

Architecture:
  - Flask runs on the main thread (keeps the process alive + satisfies Render's port check)
  - Bot runs in a supervised background thread with auto-restart on crash
  - Global exception hook catches anything that slips through
  - Health endpoint exposes bot status for UptimeRobot / Render health checks

Render start command: python server.py
"""

import logging
import os
import sys
import threading
import time
import traceback

# ── Logging — must be first, before any other imports ────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

from flask import Flask, jsonify

# ── Global state ─────────────────────────────────────────────────────────────
_bot_status = {
    "running":       False,
    "start_time":    None,
    "restart_count": 0,
    "last_error":    None,
    "last_heartbeat": None,
}
_status_lock = threading.Lock()


def _set_status(**kwargs):
    with _status_lock:
        _bot_status.update(kwargs)


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def home():
    with _status_lock:
        status = dict(_bot_status)
    return (
        f"Bot is {'running ✅' if status['running'] else 'restarting ⚠️'} | "
        f"Restarts: {status['restart_count']} | "
        f"Last error: {status['last_error'] or 'none'}"
    )


@app.route("/health")
def health():
    with _status_lock:
        status = dict(_bot_status)
    # Return 200 always — we want Render/UptimeRobot to keep the container alive.
    # Bot status is informational only.
    return jsonify({
        "status":        "ok",
        "bot_running":   status["running"],
        "restarts":      status["restart_count"],
        "last_error":    status["last_error"],
        "last_heartbeat": status["last_heartbeat"],
    }), 200


# ── Safe job wrappers (used by main.py scheduler) ────────────────────────────
# These are imported and used by main.py — centralised here so every job
# is protected by the same error boundary.

def safe_run(job_name: str, fn, *args, **kwargs):
    """
    Execute a scheduler job safely.
    Catches ALL exceptions, logs them, and never lets them propagate
    (which would kill APScheduler's job thread).
    """
    try:
        log.info("[JOB START] %s", job_name)
        fn(*args, **kwargs)
        log.info("[JOB DONE]  %s", job_name)
        _set_status(last_heartbeat=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    except Exception:
        log.error("[JOB ERROR] %s\n%s", job_name, traceback.format_exc())
        # Do NOT re-raise — scheduler stays alive


# ── Bot supervisor loop ───────────────────────────────────────────────────────

_MAX_RESTARTS     = 99999   # effectively infinite
_RESTART_DELAY_S  = 10      # seconds to wait before restarting after a crash


def _bot_supervisor():
    """
    Runs in a daemon thread.
    Imports and starts the bot, and restarts it automatically if it crashes.
    """
    # Small delay so Flask has time to bind the port before the bot does heavy work
    time.sleep(5)

    restart_count = 0

    while restart_count < _MAX_RESTARTS:
        log.info("=" * 60)
        log.info("  Bot supervisor: starting bot (attempt %d)", restart_count + 1)
        log.info("=" * 60)

        _set_status(running=True, start_time=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))

        try:
            # Import here (not at top) so any import-time errors are caught too
            from main import main
            main()
            # main() only returns on clean shutdown (KeyboardInterrupt/SystemExit)
            log.info("Bot exited cleanly.")
            break

        except SystemExit as e:
            # sys.exit(1) from _validate_config — config is broken, don't restart
            log.critical("Bot called sys.exit(%s) — likely missing env vars. Not restarting.", e.code)
            _set_status(running=False, last_error=f"sys.exit({e.code}) — check env vars")
            break

        except Exception:
            restart_count += 1
            err = traceback.format_exc()
            log.error("Bot crashed (restart #%d):\n%s", restart_count, err)
            _set_status(
                running=False,
                restart_count=restart_count,
                last_error=err.strip().splitlines()[-1],   # last line of traceback
            )
            log.info("Restarting bot in %ds...", _RESTART_DELAY_S)
            time.sleep(_RESTART_DELAY_S)

    log.critical("Bot supervisor exiting. Bot will not restart.")
    _set_status(running=False)


# ── Global exception hook ─────────────────────────────────────────────────────

def _global_exception_hook(exc_type, exc_value, exc_tb):
    """Catch any unhandled exception on the main thread and log it."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.critical(
        "UNHANDLED EXCEPTION on main thread:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )

sys.excepthook = _global_exception_hook


# ── Thread exception hook (Python 3.8+) ──────────────────────────────────────

def _thread_exception_hook(args):
    if args.exc_type is SystemExit:
        return
    log.critical(
        "UNHANDLED EXCEPTION in thread '%s':\n%s",
        args.thread.name if args.thread else "unknown",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb)),
    )

threading.excepthook = _thread_exception_hook


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    # Start the supervised bot thread (daemon=True → dies if Flask dies)
    supervisor_thread = threading.Thread(
        target=_bot_supervisor,
        daemon=True,
        name="bot-supervisor",
    )
    supervisor_thread.start()
    log.info("Bot supervisor thread started.")

    # Flask runs on the main thread — this call blocks forever.
    # As long as Flask is alive, the process stays alive.
    log.info("Flask starting on port %d...", port)
    app.run(host="0.0.0.0", port=port, threaded=True)
