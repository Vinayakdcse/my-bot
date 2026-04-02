"""
client.py - Centralized HTTP client for connection pooling and rate limit handling.
Provides a single httpx.Client instance to be used across all network requests.
"""
import httpx

http_client = httpx.Client(
    timeout=25,
    follow_redirects=True,
    headers={"User-Agent": "Mozilla/5.0 (RSS and API Fetcher)"}
)
