"""
server.py - Render deployment entry point.

Architecture:
  - Main thread: bot supervisor loop (never exits = process never dies)
  - Daemon thread: Flask health endpoint (for UptimeRobot / Render health checks)

Render service type: Background Worker
  → Never spins down, no port-binding requirement, runs indefinitely.
  → Flask still binds a port so UptimeRobot can ping it and keep logs visible.
"""

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

# ── Shared state (written by bot, read by Flask health route) ─────────────────
_state_lock = threading.Lock()
_state = {
    "running":   False,
    "restarts":  0,
    "last_err":  None,
    "heartbeat": time.time(),
}


def _set(**kw):
    with _state_lock:
        _state.update(kw)
        _state["heartbeat"] = time.time()


def _get():
    with _state_lock:
        return dict(_state)


# ── Flask health server ───────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def home():
    s = _get()
    age = int(time.time() - s["heartbeat"])
    return (
        f"{'✅ running' if s['running'] else '⚠️ restarting'} | "
        f"beat {age}s ago | restarts {s['restarts']} | "
        f"err: {s['last_err'] or 'none'}"
    )


@app.route("/health")
def health():
    s = _get()
    return jsonify({
        "ok":         True,
        "running":    s["running"],
        "beat_age_s": int(time.time() - s["heartbeat"]),
        "restarts":   s["restarts"],
        "last_error": s["last_err"],
    }), 200


# ── Exception hooks ───────────────────────────────────────────────────────────
def _excepthook(t, v, tb):
    if issubclass(t, KeyboardInterrupt):
        sys.__excepthook__(t, v, tb)
        return
    log.critical("UNHANDLED main-thread:\n%s", "".join(traceback.format_exception(t, v, tb)))


def _thread_excepthook(a):
    if a.exc_type in (SystemExit, KeyboardInterrupt):
        return
    log.critical("UNHANDLED thread '%s':\n%s",
                 getattr(a.thread, "name", "?"),
                 "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_tb)))


sys.excepthook = _excepthook
threading.excepthook = _thread_excepthook


# ── Bot runner ────────────────────────────────────────────────────────────────
# CRITICAL FIX: do NOT use importlib.reload().
# reload() re-runs module-level code but does NOT reload sub-modules
# (config, database, checkers). This leaves stale state from the previous run:
#   - Old BackgroundScheduler threads still running in background
#   - _shutdown_requested reset to False while old scheduler is still alive
#   - Module-level singletons (DB connections, HTTP clients) in undefined state
#
# Correct pattern: import bot_runner which creates everything fresh each call.
# We isolate all bot state inside the run() function — nothing at module level.

def _run_bot(set_heartbeat_fn) -> None:
    """
    Single bot run. Raises on unexpected exit so supervisor can restart.
    Returns cleanly only on KeyboardInterrupt / SIGTERM.
    """
    import bot_runner
    bot_runner.run(heartbeat_fn=set_heartbeat_fn)


# ── Supervisor ────────────────────────────────────────────────────────────────
_RESTART_DELAY = 20   # seconds to wait after a crash before restarting


def _supervisor():
    """Runs on main thread. Restarts bot indefinitely on any crash."""
    restarts = 0

    while True:
        log.info("=" * 60)
        log.info("  Supervisor: launch #%d", restarts + 1)
        log.info("=" * 60)
        _set(running=True, restarts=restarts)

        try:
            _run_bot(set_heartbeat_fn=lambda: _set(running=True))
            # bot returned cleanly → intentional shutdown, don't restart
            log.info("Bot shut down cleanly.")
            _set(running=False)
            break

        except SystemExit as e:
            log.critical("sys.exit(%s) — missing env vars? Not restarting.", e.code)
            _set(running=False, last_err=f"sys.exit({e.code})")
            break   # config is broken, restarting won't help

        except Exception:
            restarts += 1
            tb = traceback.format_exc()
            last = tb.strip().splitlines()[-1]
            log.error("Bot crashed (restart #%d):\n%s", restarts, tb)
            _set(running=False, restarts=restarts, last_err=last)
            log.info("Waiting %ds before restart...", _RESTART_DELAY)
            time.sleep(_RESTART_DELAY)

    # Supervisor exited — keep process alive so Render doesn't mark it as failed
    log.critical("Supervisor exited. Bot will not restart. Process staying alive.")
    while True:
        time.sleep(300)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    # Flask as daemon thread — process lives/dies with supervisor (main thread)
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0", port=port, threaded=True, use_reloader=False
        ),
        daemon=True,
        name="flask",
    ).start()
    log.info("Flask health server on port %d", port)

    # Supervisor on main thread — this call never returns normally
    _supervisor()
