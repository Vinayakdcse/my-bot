FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist the SQLite DB across container restarts
VOLUME ["/app/data"]
ENV DATABASE_PATH=/app/data/seen_ids.db
ENV ACCOUNTS_DB=/app/data/twscrape_accounts.db

# Use server.py so Flask binds the port before the bot starts
CMD ["python", "server.py"]
