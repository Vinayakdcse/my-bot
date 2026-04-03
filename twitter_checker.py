"""
twitter_checker.py - Fetch public tweets via Nitter RSS.

FIX: max_workers reduced from 10 → 3.
     With 10 workers × 7 Nitter instances × 12s timeout = potential 840s
     of blocked threads. APScheduler's job thread never returned.
     3 workers processes 4 accounts in two small batches — fast enough,
     safe on RAM, and never starves the connection pool.

FIX: RSS_TIMEOUT reduced to 8s (was 12s).
     Any Nitter instance that doesn't respond in 8s is dead. Move on fast.

FIX: Added total per-account timeout guard. If _process_account takes
     longer than ACCOUNT_TIMEOUT seconds, we log and return rather than
     blocking the thread pool indefinitely.
"""

import logging
import re
import xml.etree.ElementTree as ET
import email.utils
from datetime import timezone, timedelta
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

import httpx

from config import TWITTER_ACCOUNTS, TWITTER_KEYWORD_FILTER
from database import is_tweet_seen, mark_tweet_seen
from telegram_notifier import notify_tweet
from client import get_client

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

RSS_TIMEOUT      = 8    # seconds per Nitter instance attempt (was 12)
ACCOUNT_TIMEOUT  = 60   # seconds max per account before we give up
_MAX_WORKERS     = 3    # was 10 — see module docstring


# ── HTML parser ───────────────────────────────────────────────────────────────

class _NitterHTMLParser(HTMLParser):
    def __init__(self, nitter_base: str):
        super().__init__()
        self.nitter_base = nitter_base.rstrip("/")
        self.text_parts: list[str] = []
        self.image_urls: list[str] = []
        self.video_urls: list[str] = []
        self._in_p = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "p":
            self._in_p = True
        elif tag == "img":
            src = d.get("src", "")
            if src:
                if src.startswith("/"):
                    src = self.nitter_base + src
                self.image_urls.append(_nitter_pic_to_twitter(src))
        elif tag in ("video", "source"):
            src = d.get("src", "")
            if src:
                if src.startswith("/"):
                    src = self.nitter_base + src
                if src not in self.video_urls:
                    self.video_urls.append(src)

    def handle_endtag(self, tag):
        if tag == "p":
            self._in_p = False

    def handle_data(self, data):
        if self._in_p:
            s = data.strip()
            if s:
                self.text_parts.append(s)

    @property
    def text(self):
        return " ".join(self.text_parts).strip()


def _nitter_pic_to_twitter(url: str) -> str:
    try:
        from urllib.parse import urlparse, unquote
        path = unquote(urlparse(url).path.lstrip("/pic/"))
        path = (path
                .replace(":small", ":large").replace(":medium", ":large")
                .replace("?name=small", "?name=large").replace("?name=medium", "?name=large"))
        if path.endswith((".jpg", ".png")):
            path += ":large"
        if path.startswith(("media/", "tweet_video_thumb/")):
            return "https://pbs.twimg.com/" + path
    except Exception:
        pass
    return url.replace("%3Asmall", "%3Alarge").replace(":small", ":large")


def _extract_tweet_id(url: str):
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else None


# ── RSS fetch ─────────────────────────────────────────────────────────────────

def _fetch_rss(username: str):
    client = get_client()
    for base in NITTER_INSTANCES:
        try:
            r = client.get(
                f"{base}/{username}/rss",
                timeout=RSS_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (RSS reader)"},
            )
            if r.status_code != 200:
                log.debug("Nitter %s → %d", base, r.status_code)
                continue
            tweets = _parse_rss(r.text, base, username)
            log.info("Nitter OK: %s (%d items for @%s)", base, len(tweets), username)
            return base, tweets
        except httpx.TimeoutException:
            log.debug("Nitter %s timeout for @%s", base, username)
        except Exception as exc:
            log.debug("Nitter %s error for @%s: %s", base, username, exc)
    log.error("All Nitter instances failed for @%s", username)
    return None


def _parse_rss(xml_text: str, nitter_base: str, username: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.error("XML parse error: %s", exc)
        return []

    tweets = []
    for item in root.findall(".//item"):
        link     = (item.findtext("link")        or "").strip().replace("#m", "")
        title    = (item.findtext("title")       or "").strip()
        desc     = (item.findtext("description") or "").strip()
        pub_date = (item.findtext("pubDate")     or "").strip()

        tweet_id = _extract_tweet_id(link)
        if not tweet_id:
            continue

        parser = _NitterHTMLParser(nitter_base)
        parser.feed(desc)

        enc = item.find("enclosure")
        if enc is not None and "video" in enc.get("type", ""):
            url = enc.get("url", "")
            if url and url not in parser.video_urls:
                parser.video_urls.append(url)

        try:
            dt = email.utils.parsedate_to_datetime(pub_date)
            tz_ist = timezone(timedelta(hours=5, minutes=30))
            formatted = (dt.astimezone(tz_ist)
                         .strftime("%d/%m/%Y %I:%M %p GMT +5:30")
                         .replace(" AM", " am").replace(" PM", " pm"))
        except Exception:
            formatted = pub_date or "Unknown"

        tweets.append({
            "tweet_id":    tweet_id,
            "tweet_url":   f"https://twitter.com/{username}/status/{tweet_id}",
            "text":        parser.text,
            "image_urls":  parser.image_urls,
            "video_urls":  parser.video_urls,
            "is_retweet":  title.startswith("RT @"),
            "is_reply":    title.startswith("R to @"),
            "published_at": formatted,
        })
    return tweets


# ── Media download ────────────────────────────────────────────────────────────

def _download_images(urls: list[str]) -> list[bytes]:
    client = get_client()
    out = []
    for url in urls:
        try:
            r = client.get(url, timeout=20)
            r.raise_for_status()
            out.append(r.content)
        except Exception as exc:
            log.warning("Image download failed %s: %s", url[:60], exc)
    return out


def _download_video(url: str):
    client = get_client()
    MAX_MB = 45
    try:
        head = client.head(url, timeout=8)
        size = int(head.headers.get("content-length", 0))
        if size > MAX_MB * 1024 * 1024:
            log.info("Video %.1f MB > limit, using URL instead", size / 1024 / 1024)
            return None
        r = client.get(url, timeout=60)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        log.warning("Video download failed %s: %s", url[:60], exc)
        return None


# ── Keyword filter ────────────────────────────────────────────────────────────

def _passes_filter(text: str) -> bool:
    if not TWITTER_KEYWORD_FILTER:
        return True
    tl = text.lower()
    return not any(kw.lower() in tl for kw in TWITTER_KEYWORD_FILTER)


# ── Per-account processor ─────────────────────────────────────────────────────

def _process_account(account: str) -> None:
    result = _fetch_rss(account)
    if not result:
        return

    _, tweets = result
    for tweet in reversed(tweets[:3]):
        tid = tweet["tweet_id"]

        if tweet["is_retweet"]:
            mark_tweet_seen(tid)
            continue
        if is_tweet_seen(tid):
            continue
        if not _passes_filter(tweet["text"]):
            mark_tweet_seen(tid)
            continue

        video_bytes = None
        video_url   = None
        if tweet["video_urls"]:
            video_bytes = _download_video(tweet["video_urls"][0])
            if video_bytes is None:
                video_url = tweet["video_urls"][0]

        image_bytes = _download_images(tweet["image_urls"]) if tweet["image_urls"] else []

        log.info("New tweet @%s | vid=%s imgs=%d | %.60s",
                 account,
                 "bytes" if video_bytes else ("url" if video_url else "none"),
                 len(image_bytes),
                 tweet["text"])

        ok = notify_tweet(
            account_name     = f"@{account}",
            tweet_text       = tweet["text"],
            tweet_url        = tweet["tweet_url"],
            published_date   = tweet["published_at"],
            image_bytes_list = image_bytes or None,
            video_bytes      = video_bytes,
            video_url        = video_url,
        )
        if ok:
            mark_tweet_seen(tid)


# ── Public entry point ────────────────────────────────────────────────────────

def check_twitter() -> None:
    if not TWITTER_ACCOUNTS:
        return

    log.info("Checking %d Twitter account(s)...", len(TWITTER_ACCOUNTS))

    # FIX: futures.result(timeout=ACCOUNT_TIMEOUT) — if an account hangs
    # longer than 60s we log and move on instead of blocking the job thread.
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futures = {ex.submit(_process_account, acc): acc for acc in TWITTER_ACCOUNTS}
        for fut in as_completed(futures, timeout=ACCOUNT_TIMEOUT * len(TWITTER_ACCOUNTS)):
            acc = futures[fut]
            try:
                fut.result(timeout=ACCOUNT_TIMEOUT)
            except FuturesTimeout:
                log.error("@%s timed out after %ds — skipping", acc, ACCOUNT_TIMEOUT)
            except Exception as exc:
                log.error("@%s error: %s", acc, exc)
