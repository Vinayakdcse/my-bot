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

# Poll every 1 minute
YOUTUBE_POLL_INTERVAL_MINUTES = 1

# ─────────────────────────────────────────────
# TWITTER / X
# ─────────────────────────────────────────────
TWITTER_ACCOUNTS = [
    "GxTradez",
    "TTrades_edu",
    "_amtrades",
    "OTT_Trackers",
]

# Poll every 1 minute
TWITTER_POLL_INTERVAL_MINUTES = 1

TWITTER_KEYWORD_FILTER = [
    "Pay out", "discount", "prop firm", "reposted"
]

# ─────────────────────────────────────────────
# MEDIA DOWNLOAD (yt-dlp)
# ─────────────────────────────────────────────
# Max video size to download and upload to Telegram (bytes)
# Telegram's bot upload limit is 50 MB
YT_MAX_VIDEO_BYTES = 45 * 1024 * 1024   # 45 MB (safe margin)

# If video is larger than this, send thumbnail + link instead of video
YT_FALLBACK_TO_THUMBNAIL = True

# ─────────────────────────────────────────────
# GENERAL
# ─────────────────────────────────────────────
DATABASE_PATH = os.getenv("DATABASE_PATH", "seen_ids.db")
ACCOUNTS_DB   = os.getenv("ACCOUNTS_DB",   "twscrape_accounts.db")
LOG_LEVEL     = "INFO"   # DEBUG | INFO | WARNING | ERROR
