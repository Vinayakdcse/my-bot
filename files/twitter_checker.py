"""
twitter_checker.py — Fetch public tweets via Nitter RSS feeds.

How it works (exactly like YouTube):
  - Nitter is a public Twitter mirror that exposes RSS for any public account
  - We GET  https://<nitter-instance>/<username>/rss  — no login, no API key
  - Parse the XML, extract tweet ID / text / images, send to Telegram
  - Multiple Nitter instances are tried in order; if one is down the next is used

No Twitter account needed. No credentials. Completely free.
"""

import logging
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import httpx

from config import TWITTER_ACCOUNT, TWITTER_KEYWORD_FILTER
from database import is_tweet_seen, mark_tweet_seen
from telegram_notifier import notify_tweet

log = logging.getLogger(__name__)

# ── Nitter public instances (tried in order; first healthy one wins) ──────────
# Updated list — add/remove instances here if any go offline.
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.net",
    "https://nitter.it",
    "https://nitter.fdn.fr",
]

RSS_TIMEOUT = 12   # seconds per instance attempt


# ── HTML → plain text + image URL extractor ──────────────────────────────────

class _NitterHTMLParser(HTMLParser):
    """
    Nitter wraps tweet content in HTML inside <description><![CDATA[...]]>
    This parser extracts:
      - plain text  (from <p> and text nodes)
      - image URLs  (from <img src="...">)
    """

    def __init__(self, nitter_base: str):
        super().__init__()
        self.nitter_base = nitter_base.rstrip("/")
        self.text_parts: list[str] = []
        self.image_urls: list[str] = []
        self._in_p = False

    def handle_starttag(self, tag: str, attrs: list):
        if tag == "p":
            self._in_p = True
        elif tag == "img":
            attrs_dict = dict(attrs)
            src = attrs_dict.get("src", "")
            if src:
                # Nitter serves images via its own proxy (/pic/...)
                # Convert to absolute URL if relative, then to real Twitter CDN URL
                if src.startswith("/"):
                    src = self.nitter_base + src
                real_url = _nitter_pic_to_twitter(src)
                self.image_urls.append(real_url)

    def handle_endtag(self, tag: str):
        if tag == "p":
            self._in_p = False

    def handle_data(self, data: str):
        if self._in_p:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts).strip()


def _nitter_pic_to_twitter(nitter_url: str) -> str:
    """
    Convert a Nitter image proxy URL to the real Twitter CDN URL.

    Nitter format:  https://nitter.net/pic/media%2FAbCdEf.jpg%3Asmall
    Twitter format: https://pbs.twimg.com/media/AbCdEf.jpg:small

    If conversion fails, return the original URL (Nitter proxy still works).
    """
    try:
        from urllib.parse import urlparse, unquote
        path = urlparse(nitter_url).path          # e.g. /pic/media%2FAbCdEf.jpg%3Asmall
        decoded = unquote(path.lstrip("/pic/"))   # e.g.  media/AbCdEf.jpg:small
        if decoded.startswith("media/"):
            return "https://pbs.twimg.com/" + decoded
    except Exception:
        pass
    return nitter_url   # fallback: use Nitter's proxy directly


def _extract_tweet_id(url: str) -> str | None:
    """Pull the numeric tweet ID out of a Nitter or Twitter status URL."""
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else None


# ── RSS fetch with instance fallback ─────────────────────────────────────────

def _fetch_rss(username: str) -> tuple[str, list[dict]] | None:
    """
    Try each Nitter instance until one responds.
    Returns (nitter_base_url, list_of_tweet_dicts) or None if all fail.
    """
    for base in NITTER_INSTANCES:
        url = f"{base}/{username}/rss"
        try:
            r = httpx.get(
                url,
                timeout=RSS_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (RSS reader)"},
            )
            if r.status_code != 200:
                log.debug("Nitter %s → HTTP %d, trying next", base, r.status_code)
                continue

            tweets = _parse_rss(r.text, base)
            log.info("Nitter instance OK: %s  (%d items)", base, len(tweets))
            return base, tweets

        except httpx.TimeoutException:
            log.debug("Nitter %s timed out, trying next", base)
        except Exception as exc:
            log.debug("Nitter %s error: %s, trying next", base, exc)

    log.error(
        "All Nitter instances failed for @%s. "
        "Check https://status.d420.de for live instances.",
        username,
    )
    return None


def _parse_rss(xml_text: str, nitter_base: str) -> list[dict]:
    """Parse Nitter RSS XML and return a list of tweet dicts."""
    tweets = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.error("RSS XML parse error: %s", exc)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item in root.findall(".//item"):
        link  = (item.findtext("link")  or "").strip()
        title = (item.findtext("title") or "").strip()
        desc  = (item.findtext("description") or "").strip()

        tweet_id = _extract_tweet_id(link)
        if not tweet_id:
            continue

        # Skip pinned-tweet markers some instances add
        if "#m" in link:
            link = link.replace("#m", "")

        # Parse HTML description → plain text + images
        parser = _NitterHTMLParser(nitter_base)
        parser.feed(desc)

        # Nitter puts "RT @someone: ..." or "R to @someone: ..." in the title
        # to mark retweets/replies. We keep that signal to optionally filter.
        is_retweet = title.startswith("RT @")
        is_reply   = title.startswith("R to @")

        # Build the canonical Twitter URL (not Nitter URL)
        tweet_url = f"https://twitter.com/{TWITTER_ACCOUNT}/status/{tweet_id}"

        tweets.append({
            "tweet_id":   tweet_id,
            "tweet_url":  tweet_url,
            "text":       parser.text,
            "image_urls": parser.image_urls,
            "is_retweet": is_retweet,
            "is_reply":   is_reply,
        })

    return tweets


# ── Image downloader ──────────────────────────────────────────────────────────

def _download_images(urls: list[str]) -> list[bytes]:
    images = []
    for url in urls:
        try:
            r = httpx.get(url, timeout=20, follow_redirects=True)
            r.raise_for_status()
            images.append(r.content)
            log.debug("Downloaded image (%d bytes): %s", len(r.content), url[:80])
        except Exception as exc:
            log.warning("Failed to download image %s: %s", url, exc)
    return images


# ── Keyword filter ────────────────────────────────────────────────────────────

def _passes_keyword_filter(text: str) -> bool:
    if not TWITTER_KEYWORD_FILTER:
        return True
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TWITTER_KEYWORD_FILTER)


# ── Main check (called by scheduler) ─────────────────────────────────────────

def check_twitter() -> None:
    """Poll Nitter RSS for new tweets and send Telegram notifications."""
    log.info("Checking Twitter/Nitter for @%s...", TWITTER_ACCOUNT)

    result = _fetch_rss(TWITTER_ACCOUNT)
    if result is None:
        return   # all instances down — will retry next poll cycle

    _nitter_base, tweets = result

    if not tweets:
        log.info("No tweets found in RSS feed for @%s", TWITTER_ACCOUNT)
        return

    # Process oldest → newest so Telegram messages arrive in chronological order
    for tweet in reversed(tweets):
        tweet_id = tweet["tweet_id"]

        # Skip retweets (change False → True to include them)
        if tweet["is_retweet"]:
            mark_tweet_seen(tweet_id)   # mark to avoid rechecking
            continue

        if is_tweet_seen(tweet_id):
            continue

        # Keyword filter
        if not _passes_keyword_filter(tweet["text"]):
            log.debug("Tweet %s skipped by keyword filter", tweet_id)
            mark_tweet_seen(tweet_id)
            continue

        # Download images
        image_bytes = _download_images(tweet["image_urls"]) if tweet["image_urls"] else []

        log.info(
            "New tweet @%s (images=%d reply=%s): %s",
            TWITTER_ACCOUNT,
            len(image_bytes),
            tweet["is_reply"],
            tweet["text"][:80],
        )

        success = notify_tweet(
            tweet_text=tweet["text"],
            tweet_url=tweet["tweet_url"],
            image_bytes_list=image_bytes or None,
        )

        if success:
            mark_tweet_seen(tweet_id)
