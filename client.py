"""
client.py - Centralized HTTP client.

FIX: A single shared httpx.Client is NOT thread-safe when used across
     multiple concurrent threads (ThreadPoolExecutor in checkers).
     Each thread must get its own client, OR we use a thread-local client.
     We provide both: a thread-local getter and a simple factory.
"""

import threading
import httpx

_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (RSS and API Fetcher)"}
_DEFAULT_TIMEOUT = 25

# Thread-local storage — each thread gets its own client instance
_local = threading.local()


def get_client() -> httpx.Client:
    """
    Return a per-thread httpx.Client.
    Creates one on first access in each thread, reuses it after that.
    """
    if not hasattr(_local, "client") or _local.client.is_closed:
        _local.client = httpx.Client(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        )
    return _local.client


# Legacy alias — code that does `from client import http_client` still works
# but now gets the thread-local client instead of a shared one.
class _ThreadLocalClientProxy:
    """Proxy that forwards attribute access to the thread-local client."""
    def __getattr__(self, name):
        return getattr(get_client(), name)

http_client = _ThreadLocalClientProxy()
