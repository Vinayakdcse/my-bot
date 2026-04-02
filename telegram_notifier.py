"""
telegram_notifier.py - Send messages and images via Telegram Bot API.
Uses httpx for async-friendly, retry-backed HTTP calls.
"""

import io
import logging
import time
import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from client import http_client

log = logging.getLogger(__name__)

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def _retry(fn, *args, **kwargs):
    """Simple retry wrapper."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.warning("Attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    log.error("All %d attempts failed.", MAX_RETRIES)
    return None


# ── Core send functions ──────────────────────

def send_message(text: str, parse_mode: str = "HTML", preview_url: str = None) -> bool:
    """Send a plain text message."""
    def _send():
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode
        }
        if preview_url:
            payload["link_preview_options"] = {
                "url": preview_url,
                "prefer_large_media": True,
                "is_disabled": False
            }
            
        r = http_client.post(
            f"{BASE_URL}/sendMessage",
            json=payload,
        )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Message sent to Telegram.")
    return bool(result)


def send_photo(image_bytes: bytes, caption: str = "", parse_mode: str = "HTML") -> bool:
    """Send a single photo with optional caption."""
    def _send():
        r = http_client.post(
            f"{BASE_URL}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": parse_mode},
            files={"photo": ("image.jpg", io.BytesIO(image_bytes), "image/jpeg")},
        )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Photo sent to Telegram.")
    return bool(result)


def send_media_group(images: list[bytes], caption: str = "", parse_mode: str = "HTML") -> bool:
    """Send multiple images as an album. Caption goes on the first image."""
    if not images:
        return False

    media = []
    for i, _ in enumerate(images):
        item = {"type": "photo", "media": f"attach://photo{i}"}
        if i == 0 and caption:
            item["caption"] = caption
            item["parse_mode"] = parse_mode
        media.append(item)

    files = {f"photo{i}": (f"image{i}.jpg", io.BytesIO(img), "image/jpeg")
             for i, img in enumerate(images)}

    def _send():
        import json
        r = http_client.post(
            f"{BASE_URL}/sendMediaGroup",
            data={"chat_id": TELEGRAM_CHAT_ID, "media": json.dumps(media)},
            files=files,
        )
        r.raise_for_status()
        return True

    result = _retry(_send)
    if result:
        log.info("Media group (%d images) sent to Telegram.", len(images))
    return bool(result)


# ── High-level helpers ───────────────────────

def notify_youtube(video_title: str, published_date: str, video_url: str) -> bool:
    import html
    title_escaped = html.escape(video_title)
    text = (
        f"{title_escaped}\n"
        f"Date: {published_date}\n"
        f"Link: <code>{video_url}</code>"
    )
    return send_message(text, parse_mode="HTML", preview_url=video_url)


def notify_tweet(
    account_name: str,
    tweet_text: str,
    tweet_url: str,
    published_date: str,
    image_bytes_list: list[bytes] | None = None,
) -> bool:
    import html
    acc_escaped = html.escape(account_name)
    tweet_escaped = html.escape(tweet_text) if tweet_text else ""
    caption = (
        f"New Tweet from {acc_escaped}\n"
        + (f"{tweet_escaped}\n" if tweet_escaped else "")
        + f"Date: {published_date}\n"
        f"Link: <code>{tweet_url}</code>"
    )

    if image_bytes_list:
        if len(image_bytes_list) == 1:
            return send_photo(image_bytes_list[0], caption=caption, parse_mode="HTML")
        return send_media_group(image_bytes_list, caption=caption, parse_mode="HTML")

    return send_message(caption, parse_mode="HTML", preview_url=tweet_url)
