"""
server.py - Render deployment entry point.

Starts Flask first (so Render detects the open port immediately),
then starts the bot in the same process.

Render start command: python server.py
"""

import os
import time
import threading
import logging
from flask import Flask

log = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running ✅"


@app.route("/health")
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    # ── Step 1: Start Flask in a background thread so the port opens immediately ──
    # Render requires a port to be bound within ~30 seconds or it fails the deploy.
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port),
        daemon=True,
        name="flask-keepalive",
    )
    flask_thread.start()
    print(f"Flask keep-alive started on port {port}")

    # ── Step 2: Give Flask a moment to fully bind ──
    time.sleep(2)

    # ── Step 3: Start the bot (this blocks on scheduler.start()) ──
    # Import here so Flask is already up before any heavy imports run
    from main import main
    main()
