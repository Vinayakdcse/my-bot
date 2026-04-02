"""
test_twitter.py — Verify Nitter RSS fetching works end-to-end.
No login. No credentials. Just run it.

Usage:
    python test_twitter.py
    python test_twitter.py someusername   ← test a different account
"""

import sys
from twitter_checker import _fetch_rss, NITTER_INSTANCES, TWITTER_ACCOUNT

TARGET = sys.argv[1] if len(sys.argv) > 1 else TWITTER_ACCOUNT

print("=" * 60)
print(f"  Nitter RSS Test  →  @{TARGET}")
print("=" * 60)
print()
print(f"  Trying {len(NITTER_INSTANCES)} Nitter instance(s)...")
print()

result = _fetch_rss(TARGET)

if result is None:
    print("  ❌ All Nitter instances failed.")
    print()
    print("  Things to try:")
    print("  1. Check your internet connection")
    print("  2. Visit https://status.d420.de to find live Nitter instances")
    print("  3. Add working instances to NITTER_INSTANCES in twitter_checker.py")
    sys.exit(1)

base, tweets = result
print(f"  ✅ Got {len(tweets)} tweet(s) from {base}\n")

for i, t in enumerate(tweets[:5], 1):
    print(f"  Tweet {i}:")
    print(f"    ID      : {t['tweet_id']}")
    print(f"    Text    : {(t['text'] or '(no text)')[:80]}")
    print(f"    Images  : {len(t['image_urls'])}  {t['image_urls'][0][:70] if t['image_urls'] else ''}")
    print(f"    Retweet : {t['is_retweet']}   Reply: {t['is_reply']}")
    print(f"    URL     : {t['tweet_url']}")
    print()

print("  ✅ Nitter RSS is working. Run: python main.py")
