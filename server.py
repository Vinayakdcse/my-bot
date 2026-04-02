"""
server.py - Render deployment entry point.

Strategy:
  1. Flask binds the port SYNCHRONOUSLY on the main thread first.
  2. Bot runs in a background thread after Flask is confirmed up.

Render start command: python server.py
"""

import os
import time
import threading
import logging
import sys

# ── Logging (set up before any imports that might log) ───────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running ✅"


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def start_bot():
    """Runs in a background thread. Delay ensures Flask is fully up first."""
    time.sleep(5)  # wait for Flask to bind and Render to detect the port
    log.info("Starting bot in background thread...")
    try:
        from main import main
        main()
    except Exception as e:
        log.critical("Bot crashed: %s", e, exc_info=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    # Start bot in background BEFORE app.run() so the thread is queued
    bot_thread = threading.Thread(target=start_bot, daemon=True, name="bot")
    bot_thread.start()

    # app.run() blocks — Flask binds the port immediately on this line
    # Render will detect the port within seconds
    log.info("Flask binding on port %d ...", port)
    app.run(host="0.0.0.0", port=port)
