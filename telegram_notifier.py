"""
telegram_notifier.py - Send messages, photos, videos, and media groups via Telegram Bot API.

Supports:
  - send_message       : plain text with optional link preview
  - send_photo         : single image upload
  - send_video         : single video upload (playable in Telegram)
  - send_video_url     : video by URL (Telegram fetches it directly)
  - send_media_group   : album of photos and/or videos
  - notify_youtube     : YouTube notification (thumbnail + link preview)
  - notify_tweet       : tweet with photos/videos or text fallback
"""

import io
import json
import logging
import time
import html as html_module

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from client import http_client

log = logging.getLogger(__name__)

BASE_URL   = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_RETRIES  = 3
RETRY_DELAY  = 5   # seconds between retries
RATE_DELAY   = 1   # seconds between consecutive Telegram API calls

# Telegram hard limits
MAX_CAPTION_LEN  = 1024
MAX_VIDEO_BYTES  = 50 * 1024 * 1024   # 50 MB upload limit
MAX_PHOTO_BYTES  = 10 * 1024 * 1024   # 10 MB photo upload limit


# ── Internal helpers ─────────────────────────────────────────────────────────

def _trunc(text: str, limit: int = MAX_CAPTION_LEN) -> str:
    """Truncate caption to Telegram's limit."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _retry(fn, *args, **kwargs):
    """Retry a callable up to MAX_RETRIES times with exponential back-off."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # 429 = rate limited — honour Retry-After header if present
            if status == 429:
                retry_after = int(exc.response.headers.get("Retry-After", RETRY_DELAY * attempt))
                log.warning("Telegram rate-limited. Waiting %ds...", retry_after)
                time.sleep(retry_after)
            else:
                log.warning("Attempt %d/%d — HTTP %d: %s", attempt, MAX_RETRIES, status, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
        except Exception as exc:
            log.warning("Attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    log.error("All %d attempts failed.", MAX_RETRIES)
    return None


# ── Core send primitives ─────────────────────────────────────────────────────

def send_message(text: str, parse_mode: str = "HTML", preview_url: str = None) -> bool:
    """Send a plain text message, optionally with a rich link preview."""
    def _send():
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       _trunc(text, 4096),
            "parse_mode": parse_mode,
        }
        if preview_url:
            payload["link_preview_options"] = {
                "url":                preview_url,
                "prefer_large_media": True,
                "is_disabled":        False,
            }
        r = http_client.post(f"{BASE_URL}/sendMessage", json=payload)
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Message sent.")
    time.sleep(RATE_DELAY)
    return bool(result)


def send_photo(image_bytes: bytes, caption: str = "", parse_mode: str = "HTML") -> bool:
    """Upload and send a single photo."""
    if len(image_bytes) > MAX_PHOTO_BYTES:
        log.warning("Photo too large (%d MB), skipping upload.", len(image_bytes) // 1024 // 1024)
        return False

    def _send():
        r = http_client.post(
            f"{BASE_URL}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": _trunc(caption), "parse_mode": parse_mode},
            files={"photo": ("image.jpg", io.BytesIO(image_bytes), "image/jpeg")},
        )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Photo sent.")
    time.sleep(RATE_DELAY)
    return bool(result)


def send_video(video_bytes: bytes, caption: str = "", parse_mode: str = "HTML",
               thumb_bytes: bytes = None) -> bool:
    """Upload and send a video file. Falls back gracefully if too large."""
    size_mb = len(video_bytes) / 1024 / 1024
    if len(video_bytes) > MAX_VIDEO_BYTES:
        log.warning("Video too large (%.1f MB) for direct upload, skipping.", size_mb)
        return False

    def _send():
        files = {"video": ("video.mp4", io.BytesIO(video_bytes), "video/mp4")}
        if thumb_bytes:
            files["thumbnail"] = ("thumb.jpg", io.BytesIO(thumb_bytes), "image/jpeg")
        r = http_client.post(
            f"{BASE_URL}/sendVideo",
            data={
                "chat_id":            TELEGRAM_CHAT_ID,
                "caption":            _trunc(caption),
                "parse_mode":         parse_mode,
                "supports_streaming": "true",
            },
            files=files,
            timeout=120,   # large upload needs more time
        )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Video sent (%.1f MB).", size_mb)
    time.sleep(RATE_DELAY)
    return bool(result)


def send_video_url(url: str, caption: str = "", parse_mode: str = "HTML",
                   thumb_url: str = None) -> bool:
    """Tell Telegram to fetch and embed a video by URL (no local download needed)."""
    def _send():
        payload = {
            "chat_id":            TELEGRAM_CHAT_ID,
            "video":              url,
            "caption":            _trunc(caption),
            "parse_mode":         parse_mode,
            "supports_streaming": True,
        }
        if thumb_url:
            payload["thumbnail"] = thumb_url
        r = http_client.post(f"{BASE_URL}/sendVideo", json=payload)
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Video URL sent: %s", url[:80])
    time.sleep(RATE_DELAY)
    return bool(result)


def send_media_group(media_items: list[dict], caption: str = "",
                     parse_mode: str = "HTML") -> bool:
    """
    Send an album (up to 10 items).

    Each item in media_items is either:
      {"type": "photo",  "bytes": b"..."}
      {"type": "video",  "bytes": b"..."}
      {"type": "photo",  "url":   "https://..."}
      {"type": "video",  "url":   "https://..."}
    """
    if not media_items:
        return False

    # Cap at 10 (Telegram limit)
    media_items = media_items[:10]

    media_json = []
    files      = {}

    for i, item in enumerate(media_items):
        kind = item.get("type", "photo")
        entry: dict = {"type": kind}

        if "bytes" in item:
            key = f"media{i}"
            ext = "mp4" if kind == "video" else "jpg"
            mime = "video/mp4" if kind == "video" else "image/jpeg"
            files[key] = (f"file{i}.{ext}", io.BytesIO(item["bytes"]), mime)
            entry["media"] = f"attach://{key}"
            if kind == "video":
                entry["supports_streaming"] = True
        else:
            entry["media"] = item["url"]

        if i == 0 and caption:
            entry["caption"]    = _trunc(caption)
            entry["parse_mode"] = parse_mode

        media_json.append(entry)

    def _send():
        if files:
            r = http_client.post(
                f"{BASE_URL}/sendMediaGroup",
                data={"chat_id": TELEGRAM_CHAT_ID, "media": json.dumps(media_json)},
                files=files,
                timeout=120,
            )
        else:
            r = http_client.post(
                f"{BASE_URL}/sendMediaGroup",
                json={"chat_id": TELEGRAM_CHAT_ID, "media": media_json},
            )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Media group (%d items) sent.", len(media_items))
    time.sleep(RATE_DELAY)
    return bool(result)


# ── High-level notification helpers ─────────────────────────────────────────

def notify_youtube(
    video_title: str,
    published_date: str,
    video_url: str,
    channel_name: str = "",
    thumbnail_url: str = None,
    thumbnail_bytes: bytes = None,
) -> bool:
    """
    Send a YouTube notification.

    Strategy (in order):
      1. If thumbnail bytes available → sendPhoto with caption + link
      2. If thumbnail URL available  → sendPhoto by URL
      3. Fallback                    → sendMessage with rich link preview
    """
    title_esc   = html_module.escape(video_title)
    channel_esc = html_module.escape(channel_name) if channel_name else ""

    caption = ""
    if channel_esc:
        caption += f"📺 <b>{channel_esc}</b>\n"
    caption += f"🎬 {title_esc}\n"
    caption += f"📅 {published_date}\n"
    caption += f"🔗 <a href='{video_url}'>Watch on YouTube</a>"

    # Try thumbnail upload first (gives rich in-chat preview)
    if thumbnail_bytes:
        ok = send_photo(thumbnail_bytes, caption=caption)
        if ok:
            return True

    if thumbnail_url:
        def _send_thumb_url():
            r = http_client.post(
                f"{BASE_URL}/sendPhoto",
                json={
                    "chat_id":    TELEGRAM_CHAT_ID,
                    "photo":      thumbnail_url,
                    "caption":    _trunc(caption),
                    "parse_mode": "HTML",
                },
            )
            r.raise_for_status()
            return True
        result = _retry(_send_thumb_url)
        time.sleep(RATE_DELAY)
        if result:
            log.info("YouTube thumbnail photo sent.")
            return True

    # Final fallback: text message with link preview
    return send_message(caption, parse_mode="HTML", preview_url=video_url)


def notify_tweet(
    account_name: str,
    tweet_text: str,
    tweet_url: str,
    published_date: str,
    image_bytes_list: list[bytes] | None = None,
    video_bytes: bytes | None = None,
    video_url: str | None = None,
) -> bool:
    """
    Send a tweet notification.

    Priority:
      - video bytes → sendVideo (upload)
      - video URL   → sendVideo (URL)
      - images      → sendPhoto / sendMediaGroup
      - text only   → sendMessage with link preview
    """
    acc_esc    = html_module.escape(account_name)
    tweet_esc  = html_module.escape(tweet_text) if tweet_text else ""

    caption  = f"🐦 <b>{acc_esc}</b>\n"
    if tweet_esc:
        caption += f"{tweet_esc}\n"
    caption += f"📅 {published_date}\n"
    caption += f"🔗 <a href='{tweet_url}'>View Tweet</a>"

    # ── Video (uploaded bytes) ──
    if video_bytes:
        ok = send_video(video_bytes, caption=caption)
        if ok:
            return True
        log.info("Video upload failed, falling back to text.")

    # ── Video by URL ──
    if video_url:
        ok = send_video_url(video_url, caption=caption)
        if ok:
            return True
        log.info("Video URL send failed, falling back to text.")

    # ── Images ──
    if image_bytes_list:
        # Filter out oversized photos
        valid = [b for b in image_bytes_list if len(b) <= MAX_PHOTO_BYTES]
        if len(valid) == 1:
            return send_photo(valid[0], caption=caption)
        if len(valid) > 1:
            items = [{"type": "photo", "bytes": b} for b in valid]
            return send_media_group(items, caption=caption)

    # ── Text fallback ──
    return send_message(caption, parse_mode="HTML", preview_url=tweet_url)
