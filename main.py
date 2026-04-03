"""
main.py - Local run shim.

On Render:    python server.py
Locally:      python main.py  (or python bot_runner.py — same thing)

This file exists only so `python main.py` still works locally.
All real logic is in bot_runner.py.
"""

import logging
import sys
from config import LOG_LEVEL

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("notifier.log"),
    ],
)

from bot_runner import run
run()
