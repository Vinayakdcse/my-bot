"""
server.py - Render deployment entry point.

CRITICAL FIX: Render free-tier Web Services spin down after ~15 min of
no HTTP traffic, killing the bot even with UptimeRobot.

SOLUTION: This file now acts as a Background Worker wrapper.
  - On Render → set service type to "Background Worker" (never spins down)
  - Flask still runs to provide health endpoint for UptimeRobot pings
  - Bot supervisor restarts bot automatically on any crash
"""

import importlib
import logging
import os
import sys
import threading
import time
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

from flask import Flask, jsonify

# ── Shared heartbeat ──────────────────────────────────────────────────────────
_heartbeat_lock = threading.Lock()
_heartbeat: dict = {
    "ts":       time.time(),
    "restarts": 0,
    "last_err": None,
    "running":  False,
}


def update_heartbeat(**kwargs):
    with _heartbeat_lock:
        _heartbeat.update(kwargs)
        _heartbeat["ts"] = time.time()


def get_heartbeat() -> dict:
    with _heartbeat_lock:
        return dict(_heartbeat)


# ── Flask (health endpoint only) ──────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def home():
    hb = get_heartbeat()
    age = int(time.time() - hb["ts"])
    state = "✅ running" if hb["running"] else "⚠️ restarting"
    return (
        f"Bot {state} | "
        f"heartbeat {age}s ago | "
        f"restarts: {hb['restarts']} | "
        f"last error: {hb['last_err'] or 'none'}"
    )


@app.route("/health")
def health():
    hb = get_heartbeat()
    return jsonify({
        "status":          "ok",
        "bot_running":     hb["running"],
        "heartbeat_age_s": int(time.time() - hb["ts"]),
        "restarts":        hb["restarts"],
        "last_error":      hb["last_err"],
    }), 200


def _run_flask(port: int):
    """Flask runs in a background thread — just for health pings."""
    try:
        app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
    except Exception:
        log.error("Flask crashed:\n%s", traceback.format_exc())


# ── Bot supervisor ────────────────────────────────────────────────────────────
_RESTART_DELAY = 15   # seconds between crash restarts


def _run_bot_once() -> None:
    import main as main_module
    importlib.reload(main_module)
    main_module.update_heartbeat = update_heartbeat
    main_module.main()


def _bot_supervisor() -> None:
    restarts = 0
    while True:
        log.info("━" * 60)
        log.info("  Supervisor: starting bot (run #%d)", restarts + 1)
        log.info("━" * 60)
        update_heartbeat(running=True, restarts=restarts)

        try:
            _run_bot_once()
            # main() only returns cleanly on KeyboardInterrupt / SystemExit
            log.info("Bot exited cleanly.")
            update_heartbeat(running=False)
            return

        except SystemExit as exc:
            log.critical(
                "Bot called sys.exit(%s) — likely missing env vars. Not restarting.", exc.code
            )
            update_heartbeat(running=False, last_err=f"sys.exit({exc.code}) — check env vars")
            return  # fatal config error — don't loop forever

        except Exception:
            restarts += 1
            tb = traceback.format_exc()
            last_line = tb.strip().splitlines()[-1]
            log.error("Bot crashed (restart #%d):\n%s", restarts, tb)
            update_heartbeat(running=False, restarts=restarts, last_err=last_line)
            log.info("Restarting bot in %ds...", _RESTART_DELAY)
            time.sleep(_RESTART_DELAY)


# ── Exception hooks ───────────────────────────────────────────────────────────

def _excepthook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.critical(
        "UNHANDLED on main thread:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )


def _thread_excepthook(args):
    if args.exc_type in (SystemExit, KeyboardInterrupt):
        return
    log.critical(
        "UNHANDLED in thread '%s':\n%s",
        getattr(args.thread, "name", "?"),
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb)),
    )


sys.excepthook = _excepthook
threading.excepthook = _thread_excepthook


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    # Flask in background thread — keeps health endpoint alive for UptimeRobot
    flask_thread = threading.Thread(
        target=_run_flask, args=(port,), daemon=True, name="flask"
    )
    flask_thread.start()
    log.info("Flask health server started on port %d", port)

    # Bot supervisor runs on the MAIN thread — process lives as long as this runs.
    # On Render Background Worker, this never gets spun down.
    _bot_supervisor()

    # If supervisor ever exits cleanly, keep process alive (shouldn't happen)
    log.critical("Supervisor exited — holding process alive.")
    while True:
        time.sleep(60)
