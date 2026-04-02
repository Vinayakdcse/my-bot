"""
config.py - Central configuration for the notification bot.
Edit this file to add/remove YouTube channels or change the Twitter account.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# YOUTUBE
# ─────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# Add channel IDs or full URLs here.
# Channel ID looks like: UCxxxxxxxxxxxxxxxxxxxxxx
# OR paste the full channel URL — the code resolves it automatically.
YOUTUBE_CHANNELS = [
    "https://www.youtube.com/@MrBeast",            # example – replace with your channels
    "https://www.youtube.com/@mkbhd",
    # "UCxxxxxxxxxxxxxxxxxxxxxxx",                 # raw channel ID also works
]

# How often to check YouTube (in minutes)
YOUTUBE_POLL_INTERVAL_MINUTES = 15

# ─────────────────────────────────────────────
# TWITTER / X
# ─────────────────────────────────────────────
# The Twitter account to track (without @)
TWITTER_ACCOUNT = "gxttrades"

# How often to check Twitter (in minutes)
TWITTER_POLL_INTERVAL_MINUTES = 5

# Optional keyword filter — only notify if tweet contains one of these words.
# Leave empty [] to get ALL tweets.
TWITTER_KEYWORD_FILTER = [
    # "trade", "forex", "signal", "setup", "chart",
]

# ─────────────────────────────────────────────
# GENERAL
# ─────────────────────────────────────────────
DATABASE_PATH = os.getenv("DATABASE_PATH", "seen_ids.db")  # tracks sent IDs
LOG_LEVEL     = "INFO"   # DEBUG | INFO | WARNING | ERROR
