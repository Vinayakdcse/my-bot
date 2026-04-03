"""
youtube_checker.py - Poll YouTube Data API v3 for new uploads.

yt-dlp REMOVED: YouTube blocks all server-side downloads with
"Sign in to confirm you're not a bot" on any datacenter IP.
yt-dlp will never work on Render/Railway/any VPS without personal cookies.

Strategy now:
  - Fetch channel metadata + thumbnail via YouTube Data API
  - Send thumbnail photo + clickable "Watch on YouTube" link via Telegram
  - Clean, fast, zero download overhead, zero RAM spike
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import YOUTUBE_API_KEY, YOUTUBE_CHANNELS
from database import is_video_seen, mark_video_seen
from telegram_notifier import notify_youtube
from client import get_client

log = logging.getLogger(__name__)

YT_API       = "https://www.googleapis.com/youtube/v3"
_THUMB_KEYS  = ["maxres", "standard", "high", "medium", "default"]
_MAX_WORKERS = 4


# ── Channel ID resolution ────────────────────────────────────────────────────

def _resolve_channel_id(channel_input: str) -> str | None:
    client = get_client()
    if re.match(r"^UC[\w-]{22}$", channel_input):
        return channel_input
    m = re.search(r"/channel/(UC[\w-]{22})", channel_input)
    if m:
        return m.group(1)
    handle = re.search(r"@([\w.-]+)", channel_input)
    if handle:
        username = handle.group(1)
        try:
            r = client.get(
                f"{YT_API}/channels",
                params={"part": "id", "forHandle": f"@{username}", "key": YOUTUBE_API_KEY},
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if items:
                return items[0]["id"]
        except Exception as exc:
            log.error("Failed to resolve @%s: %s", username, exc)
    log.warning("Could not resolve channel: %s", channel_input)
    return None


# ── Thumbnail downloader ─────────────────────────────────────────────────────

def _fetch_thumbnail(thumbnails: dict) -> tuple[bytes | None, str | None]:
    client = get_client()
    for key in _THUMB_KEYS:
        entry = thumbnails.get(key)
        if entry and entry.get("url"):
            url = entry["url"]
            try:
                r = client.get(url, timeout=15)
                r.raise_for_status()
                return r.content, url
            except Exception as exc:
                log.debug("Thumbnail failed (%s): %s", key, exc)
    return None, None


# ── Duration helpers ─────────────────────────────────────────────────────────

def _parse_yt_duration(ds: str) -> int:
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", ds)
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def _is_short(duration_seconds: int) -> bool:
    return 0 < duration_seconds <= 60


# ── Fetch latest videos ──────────────────────────────────────────────────────

def _get_latest_videos(channel_id: str, max_results: int = 2) -> list[dict]:
    client = get_client()
    try:
        r = client.get(
            f"{YT_API}/channels",
            params={"part": "contentDetails,snippet", "id": channel_id, "key": YOUTUBE_API_KEY},
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return []

        channel_name     = items[0]["snippet"]["title"]
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        r2 = client.get(
            f"{YT_API}/playlistItems",
            params={
                "part": "snippet", "playlistId": uploads_playlist,
                "maxResults": max_results, "key": YOUTUBE_API_KEY,
            },
        )
        r2.raise_for_status()
        playlist_items = r2.json().get("items", [])
        if not playlist_items:
            return []

        video_ids = [i["snippet"]["resourceId"]["videoId"] for i in playlist_items]
        r3 = client.get(
            f"{YT_API}/videos",
            params={
                "part": "contentDetails,snippet",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY,
            },
        )
        r3.raise_for_status()
        video_details = {v["id"]: v for v in r3.json().get("items", [])}

        videos = []
        for item in playlist_items:
            snip     = item["snippet"]
            video_id = snip["resourceId"]["videoId"]
            detail   = video_details.get(video_id, {})

            duration_sec = _parse_yt_duration(
                detail.get("contentDetails", {}).get("duration", "PT0S")
            )
            is_short = _is_short(duration_sec)
            url = (
                f"https://www.youtube.com/shorts/{video_id}"
                if is_short else
                f"https://www.youtube.com/watch?v={video_id}"
            )

            raw_date = snip.get("publishedAt", "")
            try:
                dt     = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                tz_ist = timezone(timedelta(hours=5, minutes=30))
                formatted_date = (
                    dt.astimezone(tz_ist)
                    .strftime("%d/%m/%Y %I:%M %p GMT +5:30")
                    .replace(" AM", " am").replace(" PM", " pm")
                )
            except ValueError:
                formatted_date = raw_date or "Unknown"

            thumbnails = (
                detail.get("snippet", {}).get("thumbnails")
                or snip.get("thumbnails", {})
            )
            thumb_bytes, thumb_url = _fetch_thumbnail(thumbnails)

            videos.append({
                "video_id":     video_id,
                "channel_id":   channel_id,
                "channel_name": channel_name,
                "title":        snip["title"],
                "url":          url,
                "published_at": formatted_date,
                "thumb_bytes":  thumb_bytes,
                "thumb_url":    thumb_url,
                "is_short":     is_short,
            })

        return videos

    except Exception as exc:
        log.error("Error fetching channel %s: %s", channel_id, exc)
        return []


# ── Per-channel processor ────────────────────────────────────────────────────

def _process_channel(channel_input: str) -> None:
    channel_id = _resolve_channel_id(channel_input)
    if not channel_id:
        return

    for video in reversed(_get_latest_videos(channel_id)):
        if is_video_seen(video["video_id"]):
            continue

        kind = "Short" if video["is_short"] else "Video"
        log.info("New %s: %s — %s", kind, video["channel_name"], video["title"])

        success = notify_youtube(
            video_title     = video["title"],
            published_date  = video["published_at"],
            video_url       = video["url"],
            channel_name    = video["channel_name"],
            thumbnail_bytes = video["thumb_bytes"],
            thumbnail_url   = video["thumb_url"],
            video_bytes     = None,   # never download on server — always blocked
        )
        if success:
            mark_video_seen(video["video_id"], video["channel_id"])


# ── Public entry point ───────────────────────────────────────────────────────

def check_youtube_channels() -> None:
    log.info("Checking %d YouTube channel(s)...", len(YOUTUBE_CHANNELS))
    if not YOUTUBE_CHANNELS:
        return
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {executor.submit(_process_channel, ch): ch for ch in YOUTUBE_CHANNELS}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                log.error("Channel error: %s", exc)
