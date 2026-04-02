FROM python:3.11-slim

WORKDIR /app

# Install ffmpeg for yt-dlp video merging
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist SQLite DB across container restarts
VOLUME ["/app/data"]
ENV DATABASE_PATH=/app/data/seen_ids.db
ENV ACCOUNTS_DB=/app/data/twscrape_accounts.db

# Unbuffered stdout — logs appear immediately in Render dashboard
ENV PYTHONUNBUFFERED=1

# Prevent Python from writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "server.py"]
