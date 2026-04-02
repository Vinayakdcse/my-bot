# 📬 YouTube + Twitter → Telegram Notification Bot

Zero doom-scrolling. Get notified the moment a channel uploads or an account tweets.

---

## 📁 Project Structure

```
notifier/
├── main.py              ← Entry point / scheduler
├── config.py            ← All your settings (channels, intervals, filters)
├── database.py          ← SQLite — tracks seen video/tweet IDs
├── youtube_checker.py   ← YouTube polling logic
├── twitter_checker.py   ← Twitter/X scraping logic
├── telegram_notifier.py ← Sends messages + photos to Telegram
├── setup_twitter.py     ← One-time Twitter account setup
├── requirements.txt
├── .env.example         ← Copy → .env and fill in keys
├── railway.toml         ← Railway.app deployment
└── Dockerfile           ← Docker / VPS deployment
```

---

## ⚡ Quick Start (5 Steps)

### Step 1 — Install Python dependencies

```bash
pip install -r requirements.txt
```

> Requires Python 3.11+. Install from https://python.org if needed.

---

### Step 2 — Create your Telegram Bot

1. Open Telegram → search **@BotFather** → send `/newbot`
2. Give it a name (e.g. `MyNotifier`) and a username (e.g. `my_notifier_bot`)
3. BotFather gives you a **token** like `123456789:ABCdef...` — copy it
4. Start a chat with your new bot (search its username, hit Start)
5. Find your **Chat ID** by messaging **@userinfobot** on Telegram

---

### Step 3 — Get a YouTube API Key (free)

1. Go to https://console.cloud.google.com
2. Create a new project (name it anything)
3. Search **"YouTube Data API v3"** → click Enable
4. Go to **APIs & Services → Credentials → Create Credentials → API Key**
5. Copy the key

**Free quota:** 10,000 units/day. Checking 10 channels every 15 minutes uses ~960 units/day. ✅

---

### Step 4 — Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in your three values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
YOUTUBE_API_KEY=your_youtube_api_key_here
```

---

### Step 5 — Set up Twitter scraping (one-time)

twscrape uses your own Twitter account(s) to scrape — no official API key needed.
A single alt/burner account works perfectly.

```bash
python setup_twitter.py
```

Follow the prompts: enter your Twitter username, password, and email.
The session is saved locally (you won't need to do this again).

---

### Run it!

```bash
python main.py
```

You'll see logs in the terminal and in `notifier.log`.
On first start, it checks immediately, then polls on schedule.

---

## ⚙️ Configuration

Open **`config.py`** to customize everything.

### Adding / removing YouTube channels

```python
YOUTUBE_CHANNELS = [
    "https://www.youtube.com/@MrBeast",
    "https://www.youtube.com/@mkbhd",
    "https://www.youtube.com/@channelname",   # ← add more here
]
```

Accepts:
- `https://www.youtube.com/@handle`
- `https://www.youtube.com/channel/UCxxxxxxx`
- Raw channel ID: `UCxxxxxxxxxxxxxxxxxxxxxxx`

### Changing the Twitter account

```python
TWITTER_ACCOUNT = "gxttrades"   # ← change to any @username (without @)
```

### Keyword filtering (optional)

Only get notified if the tweet contains these words:

```python
TWITTER_KEYWORD_FILTER = ["trade", "forex", "signal", "setup", "chart"]
```

Leave it empty `[]` to receive all tweets.

### Poll intervals

```python
YOUTUBE_POLL_INTERVAL_MINUTES = 15   # check YouTube every 15 min
TWITTER_POLL_INTERVAL_MINUTES = 5    # check Twitter every 5 min
```

---

## 🚀 Deployment (Keep it Running 24/7)

### Option A — Railway.app (Easiest, Free tier available)

1. Push your code to a GitHub repo (don't include `.env` — add to `.gitignore`)
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Add your environment variables in the Railway dashboard under **Variables**:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `YOUTUBE_API_KEY`
4. Railway auto-detects `railway.toml` and starts `python main.py`

⚠️ **Important:** Before deploying to Railway, run `setup_twitter.py` locally first,
then upload the generated `twscrape_accounts.db` file to Railway's persistent storage,
or use a Volume to persist it.

---

### Option B — VPS (DigitalOcean, Hetzner, etc. ~$4/month)

```bash
# SSH into your server
git clone https://github.com/yourusername/notifier.git
cd notifier
pip install -r requirements.txt
cp .env.example .env && nano .env   # fill in keys

# Setup Twitter accounts
python setup_twitter.py

# Run as a background service with systemd
sudo nano /etc/systemd/system/notifier.service
```

Paste this into the service file:

```ini
[Unit]
Description=YouTube + Twitter Telegram Notifier
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/notifier
ExecStart=/usr/bin/python3 /home/ubuntu/notifier/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable notifier
sudo systemctl start notifier
sudo systemctl status notifier   # check it's running

# View live logs
journalctl -u notifier -f
```

---

### Option C — Run locally (your PC / Mac)

Just run `python main.py`. Keep the terminal open, or use `tmux`/`screen` to keep it running after you close the window.

For Mac, use `launchd`. For Windows, use Task Scheduler.

---

## 🔍 Troubleshooting

| Problem | Fix |
|---|---|
| `TELEGRAM_BOT_TOKEN missing` | Fill in your `.env` file |
| YouTube gives quota errors | Reduce channels or increase `YOUTUBE_POLL_INTERVAL_MINUTES` |
| Twitter not fetching tweets | Re-run `setup_twitter.py` — session may have expired |
| Duplicate notifications | SQLite DB (`seen_ids.db`) may have been deleted — it auto-recreates |
| Bot sends nothing on startup | Normal — it only notifies for NEW content after the first run |

---

## 🛡️ Anti-Spam Features Built In

- **Deduplication:** Every sent video/tweet ID is stored in `seen_ids.db`
- **Oldest-first ordering:** If multiple new items appear, they arrive in chronological order
- **Keyword filter:** Reduce Twitter noise with `TWITTER_KEYWORD_FILTER`
- **Retweet skip:** Retweets are skipped by default (edit `twitter_checker.py` to change)

---

## 📦 Dependencies Explained

| Package | Purpose |
|---|---|
| `apscheduler` | Runs jobs on a schedule (like cron, but in Python) |
| `httpx` | Modern HTTP client with timeout + retry support |
| `python-dotenv` | Loads `.env` file into environment variables |
| `google-api-python-client` | Official YouTube Data API v3 client |
| `twscrape` | Scrapes Twitter/X without needing the paid API |

---

## 🔒 Security Notes

- **Never** commit your `.env` file to git. Add `.env` to `.gitignore`.
- The `twscrape_accounts.db` file contains your Twitter session — keep it private.
- Your YouTube API key is free-tier limited — it can't be abused for significant cost.
