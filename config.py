"""
config.py - Central configuration for the notification bot.
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

YOUTUBE_CHANNELS = [
    "https://www.youtube.com/@TTrades_edu",
    "https://www.youtube.com/@GxTradez",
    "https://www.youtube.com/@amtrades",
    "https://www.youtube.com/@Mrwhosetheboss",
    "https://www.youtube.com/@HubermanLabClips",
    "https://www.youtube.com/@Fireship",
    "https://www.youtube.com/@HANNAHFOREX",
]

# 15 min = safe for YouTube API quota (10,000 units/day free)
# 7 channels × ~3 API calls × 96 polls/day = ~2016 units/day (well within limit)
# DO NOT set below 10 — you will exhaust the daily quota in hours
YOUTUBE_POLL_INTERVAL_MINUTES = 15

# ─────────────────────────────────────────────
# TWITTER / X
# ─────────────────────────────────────────────
TWITTER_ACCOUNTS = [
    "GxTradez",
    "TTrades_edu",
    "_amtrades",
    "OTT_Trackers",
]

# 5 min is fine for Nitter RSS (no API quota)
TWITTER_POLL_INTERVAL_MINUTES = 5

TWITTER_KEYWORD_FILTER = [
    "Pay out", "discount", "prop firm", "reposted"
]

# ─────────────────────────────────────────────
# GENERAL
# ─────────────────────────────────────────────
DATABASE_PATH = os.getenv("DATABASE_PATH", "seen_ids.db")
ACCOUNTS_DB   = os.getenv("ACCOUNTS_DB",   "twscrape_accounts.db")
LOG_LEVEL     = "INFO"
