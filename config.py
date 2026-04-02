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
    "https://www.youtube.com/@TTrades_edu",            # example – replace with your channels
    "https://www.youtube.com/@GxTradez",
    "https://www.youtube.com/@amtrades",
    "https://www.youtube.com/@Mrwhosetheboss",
    "https://www.youtube.com/@HubermanLabClips",
    "https://www.youtube.com/@Fireship",
    "https://www.youtube.com/@HANNAHFOREX",
    # "UCxxxxxxxxxxxxxxxxxxxxxxx",                 # raw channel ID also works
]

# How often to check YouTube (in minutes)
YOUTUBE_POLL_INTERVAL_MINUTES = 60

# ─────────────────────────────────────────────
# TWITTER / X
# ─────────────────────────────────────────────
# The Twitter accounts to track (without @)
TWITTER_ACCOUNTS = [
    "GxTradez",
    "TTrades_edu",
    "_amtrades",
    "OTT_Trackers",
]

# How often to check Twitter (in minutes)
TWITTER_POLL_INTERVAL_MINUTES = 20

# Optional keyword filter — exlude tweets that contain ANY of these words.
# Leave empty [] to get ALL tweets.
TWITTER_KEYWORD_FILTER = [
    "Pay out", "discount",  "prop firm", "reposted"
]

# ─────────────────────────────────────────────
# INSTAGRAM
# ─────────────────────────────────────────────
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")

# The Instagram accounts to track (without @)
INSTAGRAM_ACCOUNTS = [
    "cristiano",
    "therock",
]

# How often to check Instagram (in minutes)
INSTAGRAM_POLL_INTERVAL_MINUTES = 30

# ─────────────────────────────────────────────
# GENERAL
# ─────────────────────────────────────────────
DATABASE_PATH  = os.getenv("DATABASE_PATH",  "seen_ids.db")          # tracks sent IDs
ACCOUNTS_DB    = os.getenv("ACCOUNTS_DB",    "twscrape_accounts.db")  # Twitter sessions
LOG_LEVEL      = "INFO"   # DEBUG | INFO | WARNING | ERROR
