import httpx
from twitter_checker import NITTER_INSTANCES

for base in NITTER_INSTANCES:
    try:
        r = httpx.get(f"{base}/i/communities/1931413086323327066/rss", timeout=5, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (RSS reader)"})
        print(f"{base} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"{base} -> error: {e}")
