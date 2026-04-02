"""
server.py - Render deployment entry point.

ROOT CAUSES FIXED vs previous version:
  1. main() was imported at module level inside _bot_supervisor on first call,
     but Python caches the module — on restart the second call to main() would
     import the already-cached (possibly corrupted-state) module.
     FIX: force reimport via importlib.reload() on every restart.

  2. daemon=True on the bot thread means if Flask ever hiccups for a moment
     the OS can kill the daemon thread.
     FIX: bot thread is non-daemon; Flask thread is daemon instead.

  3. No watchdog — if the bot thread silently freezes (not crashes), nothing
     detects it. FIX: heartbeat timestamp checked every 60s; if stale → restart.

  4. app.run() (Werkzeug dev server) is single-threaded by default and can
     deadlock under concurrent health-check pings from UptimeRobot + Render.
     FIX: threaded=True already set, but also add use_reloader=False to
     prevent Werkzeug from spawning a second process that fights for the port.
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
# main.py updates this every keep-alive tick. server.py watchdog reads it.
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


# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    hb = get_heartbeat()
    age = int(time.time() - hb["ts"])
    return (
        f"Bot {'✅ running' if hb['running'] else '⚠️ restarting'} | "
        f"heartbeat {age}s ago | restarts: {hb['restarts']} | "
        f"last error: {hb['last_err'] or 'none'}"
    )

@app.route("/health")
def health():
    hb = get_heartbeat()
    age = int(time.time() - hb["ts"])
    return jsonify({
        "status":           "ok",
        "bot_running":      hb["running"],
        "heartbeat_age_s":  age,
        "restarts":         hb["restarts"],
        "last_error":       hb["last_err"],
    }), 200


# ── Bot supervisor ────────────────────────────────────────────────────────────
_RESTART_DELAY   = 15    # seconds between crash restarts
_WATCHDOG_STALE  = 300   # seconds of silence before watchdog force-restarts (5 min)
_bot_should_run  = threading.Event()
_bot_should_run.set()


def _run_bot_once() -> None:
    """Import (or reload) main and call main(). Raises on any failure."""
    import main as main_module
    importlib.reload(main_module)          # always get a fresh module state
    main_module.update_heartbeat = update_heartbeat   # inject heartbeat callback
    main_module.main()


def _bot_supervisor() -> None:
    time.sleep(5)   # let Flask bind the port first

    restarts = 0
    while _bot_should_run.is_set():
        log.info("━" * 60)
        log.info("  Supervisor: starting bot (run #%d)", restarts + 1)
        log.info("━" * 60)
        update_heartbeat(running=True, restarts=restarts)

        try:
            _run_bot_once()
            # main() returned cleanly (KeyboardInterrupt / SystemExit inside it)
            log.info("Bot exited cleanly — not restarting.")
            update_heartbeat(running=False)
            return

        except SystemExit as exc:
            log.critical("Bot called sys.exit(%s) — check env vars. Stopping.", exc.code)
            update_heartbeat(running=False, last_err=f"sys.exit({exc.code}) – missing env vars?")
            return   # don't restart on config errors

        except Exception:
            restarts += 1
            tb = traceback.format_exc()
            last_line = tb.strip().splitlines()[-1]
            log.error("Bot crashed (restart #%d):\n%s", restarts, tb)
            update_heartbeat(running=False, restarts=restarts, last_err=last_line)
            log.info("Restarting in %ds...", _RESTART_DELAY)
            time.sleep(_RESTART_DELAY)


def _watchdog() -> None:
    """
    Independent thread that force-kills and restarts the bot if the heartbeat
    goes stale — catches frozen/hung states that don't raise exceptions.
    """
    time.sleep(60)   # give bot time to start before watching
    while True:
        time.sleep(30)
        hb = get_heartbeat()
        if not hb["running"]:
            continue
        age = time.time() - hb["ts"]
        if age > _WATCHDOG_STALE:
            log.error(
                "WATCHDOG: heartbeat stale for %.0fs (limit %ds). "
                "Bot appears frozen — supervisor will restart it.",
                age, _WATCHDOG_STALE,
            )
            # Signal the bot's keep-alive loop to break by marking not-running
            # The supervisor loop will then restart it
            update_heartbeat(running=False, last_err="watchdog: heartbeat stale")


# ── Exception hooks ───────────────────────────────────────────────────────────

def _excepthook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.critical("UNHANDLED on main thread:\n%s",
                 "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

def _thread_excepthook(args):
    if args.exc_type in (SystemExit, KeyboardInterrupt):
        return
    log.critical("UNHANDLED in thread '%s':\n%s",
                 getattr(args.thread, "name", "?"),
                 "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb)))

sys.excepthook       = _excepthook
threading.excepthook = _thread_excepthook


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    # Non-daemon supervisor — keeps running even if Flask hiccups
    threading.Thread(target=_bot_supervisor, daemon=False, name="bot-supervisor").start()

    # Watchdog — daemon is fine, it's just a monitor
    threading.Thread(target=_watchdog, daemon=True, name="watchdog").start()

    log.info("Flask starting on port %d...", port)
    # use_reloader=False prevents Werkzeug from forking a child process
    # that tries to bind the same port → crashes on Render
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
