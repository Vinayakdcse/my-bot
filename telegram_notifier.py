"""
telegram_notifier.py - Send messages, photos, videos, and media groups via Telegram Bot API.

notify_youtube() strategy:
  1. video_bytes provided  → sendVideo (plays natively, has scrubbar in Telegram)
  2. no video / too large  → sendPhoto (thumbnail) with Watch link — rich card, no dead player
  3. no thumbnail either   → sendMessage with link preview

notify_tweet() strategy:
  1. video_bytes  → sendVideo
  2. video_url    → sendVideo by URL
  3. images       → sendPhoto / sendMediaGroup
  4. text only    → sendMessage with link preview
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

BASE_URL        = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_RETRIES     = 3
RETRY_DELAY     = 5
RATE_DELAY      = 1        # seconds between Telegram API calls
MAX_CAPTION_LEN = 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024   # 50 MB hard Telegram limit
MAX_PHOTO_BYTES = 10 * 1024 * 1024   # 10 MB


# ── Helpers ──────────────────────────────────────────────────────────────────

def _trunc(text: str, limit: int = MAX_CAPTION_LEN) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _retry(fn, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                wait = int(exc.response.headers.get("Retry-After", RETRY_DELAY * attempt))
                log.warning("Rate-limited by Telegram. Waiting %ds...", wait)
                time.sleep(wait)
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
    if len(image_bytes) > MAX_PHOTO_BYTES:
        log.warning("Photo too large (%.1f MB), skipping.", len(image_bytes) / 1024 / 1024)
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


def send_photo_url(url: str, caption: str = "", parse_mode: str = "HTML") -> bool:
    """Ask Telegram to fetch a photo by URL (no local download needed)."""
    def _send():
        r = http_client.post(
            f"{BASE_URL}/sendPhoto",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "photo":      url,
                "caption":    _trunc(caption),
                "parse_mode": parse_mode,
            },
        )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Photo URL sent.")
    time.sleep(RATE_DELAY)
    return bool(result)


def send_video(
    video_bytes: bytes,
    caption: str = "",
    parse_mode: str = "HTML",
    thumb_bytes: bytes = None,
) -> bool:
    """Upload a video file — plays natively inside Telegram (desktop + web + mobile)."""
    size_mb = len(video_bytes) / 1024 / 1024
    if len(video_bytes) > MAX_VIDEO_BYTES:
        log.warning("Video %.1f MB exceeds Telegram limit, cannot upload.", size_mb)
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
            timeout=180,   # large upload needs extra time
        )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Video uploaded (%.1f MB).", size_mb)
    time.sleep(RATE_DELAY)
    return bool(result)


def send_video_url(url: str, caption: str = "", parse_mode: str = "HTML",
                   thumb_url: str = None) -> bool:
    """Ask Telegram to fetch a video by URL."""
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
        log.info("Video URL sent.")
    time.sleep(RATE_DELAY)
    return bool(result)


def send_media_group(media_items: list[dict], caption: str = "",
                     parse_mode: str = "HTML") -> bool:
    """
    Send an album (up to 10 items).
    Each item: {"type": "photo"|"video", "bytes": b"..."} or {"type": ..., "url": "..."}
    """
    if not media_items:
        return False

    media_items = media_items[:10]
    media_json  = []
    files       = {}

    for i, item in enumerate(media_items):
        kind  = item.get("type", "photo")
        entry = {"type": kind}

        if "bytes" in item:
            key  = f"media{i}"
            ext  = "mp4" if kind == "video" else "jpg"
            mime = "video/mp4" if kind == "video" else "image/jpeg"
            files[key]    = (f"file{i}.{ext}", io.BytesIO(item["bytes"]), mime)
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
                timeout=180,
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
    thumbnail_bytes: bytes = None,
    thumbnail_url: str = None,
    video_bytes: bytes = None,
) -> bool:
    """
    Send a YouTube video notification.

    Priority:
      1. video_bytes available  → sendVideo (fully playable in Telegram)
      2. thumbnail available    → sendPhoto (rich card preview) + link
      3. fallback               → sendMessage with link preview
    """
    title_esc   = html_module.escape(video_title)
    channel_esc = html_module.escape(channel_name) if channel_name else ""

    caption  = ""
    if channel_esc:
        caption += f"📺 <b>{channel_esc}</b>\n"
    caption += f"🎬 {title_esc}\n"
    caption += f"📅 {published_date}\n"
    caption += f"🔗 <a href='{video_url}'>Watch on YouTube</a>"

    # ── 1. Playable video upload ──
    if video_bytes:
        log.info("Sending YouTube video as uploadable file (%.1f MB).",
                 len(video_bytes) / 1024 / 1024)
        ok = send_video(video_bytes, caption=caption, thumb_bytes=thumbnail_bytes)
        if ok:
            return True
        log.info("Video upload failed, falling back to thumbnail.")

    # ── 2. Thumbnail photo (rich card, user taps link to watch) ──
    if thumbnail_bytes:
        ok = send_photo(thumbnail_bytes, caption=caption)
        if ok:
            return True

    if thumbnail_url:
        ok = send_photo_url(thumbnail_url, caption=caption)
        if ok:
            return True

    # ── 3. Text fallback ──
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

    Priority: video upload → video URL → images → text+preview
    """
    acc_esc   = html_module.escape(account_name)
    tweet_esc = html_module.escape(tweet_text) if tweet_text else ""

    caption  = f"🐦 <b>{acc_esc}</b>\n"
    if tweet_esc:
        caption += f"{tweet_esc}\n"
    caption += f"📅 {published_date}\n"
    caption += f"🔗 <a href='{tweet_url}'>View Tweet</a>"

    # ── Video upload ──
    if video_bytes:
        ok = send_video(video_bytes, caption=caption)
        if ok:
            return True
        log.info("Tweet video upload failed, trying URL.")

    # ── Video by URL ──
    if video_url:
        ok = send_video_url(video_url, caption=caption)
        if ok:
            return True
        log.info("Tweet video URL failed, falling back to images/text.")

    # ── Images ──
    if image_bytes_list:
        valid = [b for b in image_bytes_list if len(b) <= MAX_PHOTO_BYTES]
        if len(valid) == 1:
            return send_photo(valid[0], caption=caption)
        if len(valid) > 1:
            items = [{"type": "photo", "bytes": b} for b in valid]
            return send_media_group(items, caption=caption)

    # ── Text fallback ──
    return send_message(caption, parse_mode="HTML", preview_url=tweet_url)
