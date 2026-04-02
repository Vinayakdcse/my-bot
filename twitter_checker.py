"""
twitter_checker.py - Fetch public tweets via Nitter RSS feeds.

Changes vs original:
  - Extracts video URLs from Nitter RSS <enclosure> tags and <video> elements
  - Passes video_url to notify_tweet() for in-chat video playback
  - Images and videos handled separately so sendMediaGroup uses correct types
  - All other logic (Nitter fallback, keyword filter, dedup) unchanged
"""

import logging
import re
import xml.etree.ElementTree as ET
import email.utils
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from config import TWITTER_ACCOUNTS, TWITTER_KEYWORD_FILTER
from database import is_tweet_seen, mark_tweet_seen
from telegram_notifier import notify_tweet
from client import http_client

log = logging.getLogger(__name__)

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.net",
    "https://nitter.it",
    "https://nitter.fdn.fr",
]

RSS_TIMEOUT = 12


# ── HTML parser — extracts text + image URLs + video URLs ────────────────────

class _NitterHTMLParser(HTMLParser):
    def __init__(self, nitter_base: str):
        super().__init__()
        self.nitter_base = nitter_base.rstrip("/")
        self.text_parts: list[str] = []
        self.image_urls: list[str] = []
        self.video_urls: list[str] = []
        self._in_p = False

    def handle_starttag(self, tag: str, attrs: list):
        attrs_dict = dict(attrs)

        if tag == "p":
            self._in_p = True

        elif tag == "img":
            src = attrs_dict.get("src", "")
            if src:
                if src.startswith("/"):
                    src = self.nitter_base + src
                self.image_urls.append(_nitter_pic_to_twitter(src))

        elif tag in ("video", "source"):
            # <video src="..."> or <source src="..." type="video/mp4">
            src = attrs_dict.get("src", "")
            if src:
                if src.startswith("/"):
                    src = self.nitter_base + src
                if src not in self.video_urls:
                    self.video_urls.append(src)

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
    try:
        from urllib.parse import urlparse, unquote
        path    = urlparse(nitter_url).path
        decoded = unquote(path.lstrip("/pic/"))
        decoded = (decoded
                   .replace(":small", ":large")
                   .replace(":medium", ":large")
                   .replace("?name=small", "?name=large")
                   .replace("?name=medium", "?name=large"))
        if decoded.endswith((".jpg", ".png")):
            decoded += ":large"
        if decoded.startswith(("media/", "tweet_video_thumb/")):
            return "https://pbs.twimg.com/" + decoded
    except Exception:
        pass
    return nitter_url.replace("%3Asmall", "%3Alarge").replace(":small", ":large")


def _extract_tweet_id(url: str) -> str | None:
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else None


# ── RSS fetch with instance fallback ────────────────────────────────────────

def _fetch_rss(username: str) -> tuple[str, list[dict]] | None:
    for base in NITTER_INSTANCES:
        url = f"{base}/{username}/rss"
        try:
            r = http_client.get(
                url,
                timeout=RSS_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (RSS reader)"},
            )
            if r.status_code != 200:
                log.debug("Nitter %s → HTTP %d, trying next", base, r.status_code)
                continue
            tweets = _parse_rss(r.text, base, username)
            log.info("Nitter OK: %s  (%d items)", base, len(tweets))
            return base, tweets
        except httpx.TimeoutException:
            log.debug("Nitter %s timed out, trying next", base)
        except Exception as exc:
            log.debug("Nitter %s error: %s, trying next", base, exc)

    log.error("All Nitter instances failed for @%s.", username)
    return None


def _parse_rss(xml_text: str, nitter_base: str, username: str) -> list[dict]:
    tweets = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.error("RSS XML parse error: %s", exc)
        return []

    for item in root.findall(".//item"):
        link     = (item.findtext("link")        or "").strip()
        title    = (item.findtext("title")       or "").strip()
        desc     = (item.findtext("description") or "").strip()
        pub_date = (item.findtext("pubDate")     or "").strip()

        tweet_id = _extract_tweet_id(link)
        if not tweet_id:
            continue

        link = link.replace("#m", "")

        # Parse HTML → text + images + videos
        parser = _NitterHTMLParser(nitter_base)
        parser.feed(desc)

        # Also check <enclosure> for video/audio attachments
        enclosure = item.find("enclosure")
        enclosure_url = None
        if enclosure is not None:
            enc_type = enclosure.get("type", "")
            enc_url  = enclosure.get("url", "")
            if "video" in enc_type and enc_url:
                enclosure_url = enc_url
                if enc_url not in parser.video_urls:
                    parser.video_urls.append(enc_url)

        is_retweet = title.startswith("RT @")
        is_reply   = title.startswith("R to @")

        tweet_url = f"https://twitter.com/{username}/status/{tweet_id}"

        # Format date
        try:
            dt     = email.utils.parsedate_to_datetime(pub_date)
            tz_ist = timezone(timedelta(hours=5, minutes=30))
            formatted_date = (
                dt.astimezone(tz_ist)
                .strftime("%d/%m/%Y %I:%M %p GMT +5:30")
                .replace(" AM", " am")
                .replace(" PM", " pm")
            )
        except Exception:
            formatted_date = pub_date or "Unknown date"

        tweets.append({
            "tweet_id":   tweet_id,
            "tweet_url":  tweet_url,
            "text":       parser.text,
            "image_urls": parser.image_urls,
            "video_urls": parser.video_urls,   # NEW
            "is_retweet": is_retweet,
            "is_reply":   is_reply,
            "published_at": formatted_date,
        })

    return tweets


# ── Media downloaders ────────────────────────────────────────────────────────

def _download_images(urls: list[str]) -> list[bytes]:
    images = []
    for url in urls:
        try:
            r = http_client.get(url, timeout=20, follow_redirects=True)
            r.raise_for_status()
            images.append(r.content)
            log.debug("Image downloaded (%d bytes)", len(r.content))
        except Exception as exc:
            log.warning("Image download failed %s: %s", url[:80], exc)
    return images


def _download_video(url: str) -> bytes | None:
    """Download a tweet video. Returns None if too large or failed."""
    MAX_VIDEO_DOWNLOAD_MB = 45
    try:
        # HEAD request to check size first
        head = http_client.head(url, timeout=10, follow_redirects=True)
        content_length = int(head.headers.get("content-length", 0))
        if content_length > MAX_VIDEO_DOWNLOAD_MB * 1024 * 1024:
            log.info(
                "Video too large (%.1f MB), will send URL instead.",
                content_length / 1024 / 1024,
            )
            return None
        r = http_client.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        log.debug("Video downloaded (%d bytes)", len(r.content))
        return r.content
    except Exception as exc:
        log.warning("Video download failed %s: %s", url[:80], exc)
        return None


# ── Keyword filter ───────────────────────────────────────────────────────────

def _passes_keyword_filter(text: str) -> bool:
    if not TWITTER_KEYWORD_FILTER:
        return True
    text_lower = text.lower()
    return not any(kw.lower() in text_lower for kw in TWITTER_KEYWORD_FILTER)


# ── Per-account processor ────────────────────────────────────────────────────

def _process_account(account: str) -> None:
    log.info("Checking Twitter/Nitter for @%s...", account)

    result = _fetch_rss(account)
    if result is None:
        return

    _nitter_base, tweets = result
    if not tweets:
        log.info("No tweets found for @%s", account)
        return

    for tweet in reversed(tweets[:3]):   # oldest first, max 3
        tweet_id = tweet["tweet_id"]

        if tweet["is_retweet"]:
            mark_tweet_seen(tweet_id)
            continue

        if is_tweet_seen(tweet_id):
            continue

        if not _passes_keyword_filter(tweet["text"]):
            log.debug("Tweet %s filtered by keyword", tweet_id)
            mark_tweet_seen(tweet_id)
            continue

        # ── Media resolution ──────────────────────────────────────────────
        video_bytes = None
        video_url   = None

        if tweet["video_urls"]:
            first_video = tweet["video_urls"][0]
            video_bytes = _download_video(first_video)
            if video_bytes is None:
                # Too large → pass URL so Telegram fetches it
                video_url = first_video

        image_bytes = _download_images(tweet["image_urls"]) if tweet["image_urls"] else []

        log.info(
            "New tweet @%s | video=%s | images=%d | reply=%s | %s",
            account,
            "bytes" if video_bytes else ("url" if video_url else "none"),
            len(image_bytes),
            tweet["is_reply"],
            tweet["text"][:80],
        )

        success = notify_tweet(
            account_name     = f"@{account}",
            tweet_text       = tweet["text"],
            tweet_url        = tweet["tweet_url"],
            published_date   = tweet["published_at"],
            image_bytes_list = image_bytes or None,
            video_bytes      = video_bytes,
            video_url        = video_url,
        )

        if success:
            mark_tweet_seen(tweet_id)


# ── Public entry point ───────────────────────────────────────────────────────

def check_twitter() -> None:
    """Poll Nitter RSS for new tweets and send Telegram notifications."""
    if not TWITTER_ACCOUNTS:
        return

    with ThreadPoolExecutor(max_workers=min(len(TWITTER_ACCOUNTS), 10)) as executor:
        futures = {executor.submit(_process_account, acc): acc for acc in TWITTER_ACCOUNTS}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                log.error("Exception in twitter worker: %s", e)
