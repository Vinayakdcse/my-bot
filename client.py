"""
client.py - Thread-safe HTTP client with connection limits.

FIX: Default httpx.Client has a connection pool of 10 per host.
     When TwitterChecker runs 4 concurrent threads, each making multiple
     requests to Telegram + Nitter simultaneously, the pool exhausts.
     Threads block waiting for a connection — APScheduler job never returns —
     max_instances=1 means next scheduled run is skipped forever.

FIX: Each thread gets its own httpx.Client (thread-local).
     No shared pool = no contention = no silent hangs.
"""

import threading
import httpx

_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NotificationBot/1.0)"}
_DEFAULT_TIMEOUT = httpx.Timeout(
    connect=10.0,   # fail fast on dead hosts
    read=30.0,
    write=30.0,
    pool=5.0,       # don't wait more than 5s for a pool slot
)

_local = threading.local()


def get_client() -> httpx.Client:
    """Per-thread httpx.Client. Created on first access, reused within thread."""
    client = getattr(_local, "client", None)
    if client is None or client.is_closed:
        _local.client = httpx.Client(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=30,
            ),
        )
    return _local.client


# Backward-compat proxy — `from client import http_client` still works
class _Proxy:
    def __getattr__(self, name):
        return getattr(get_client(), name)


http_client = _Proxy()
