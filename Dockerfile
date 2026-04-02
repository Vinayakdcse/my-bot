FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist the SQLite DB and twscrape session across container restarts
VOLUME ["/app/data"]
ENV DATABASE_PATH=/app/data/seen_ids.db
ENV ACCOUNTS_DB=/app/data/twscrape_accounts.db

CMD ["python", "main.py"]
