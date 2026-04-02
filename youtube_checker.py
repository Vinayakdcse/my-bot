"""
youtube_checker.py - Poll YouTube Data API v3 for new uploads.

Free quota: 10,000 units/day.
Each channel check costs ~3 units → you can check ~3,300 channels/day.
At a 15-min interval, 10 channels = 960 units/day — well within limits.
"""

import logging
import re
import httpx
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import YOUTUBE_API_KEY, YOUTUBE_CHANNELS
from database import is_video_seen, mark_video_seen
from telegram_notifier import notify_youtube
from client import http_client

log = logging.getLogger(__name__)

YT_API = "https://www.googleapis.com/youtube/v3"


# ── Channel ID resolution ────────────────────

def _resolve_channel_id(channel_input: str) -> str | None:
    """
    Accept either:
      - A raw channel ID (UCxxxxxxx)
      - A @handle URL  (https://www.youtube.com/@handle)
      - A channel URL  (https://www.youtube.com/channel/UCxxxxxx)
    Returns the channel ID string or None on failure.
    """
    # Already a raw ID
    if re.match(r"^UC[\w-]{22}$", channel_input):
        return channel_input

    # Extract from URL
    m = re.search(r"/channel/(UC[\w-]{22})", channel_input)
    if m:
        return m.group(1)

    # @handle — use search API to resolve
    handle = re.search(r"@([\w.-]+)", channel_input)
    if handle:
        username = handle.group(1)
        try:
            r = http_client.get(
                f"{YT_API}/channels",
                params={
                    "part": "id",
                    "forHandle": f"@{username}",
                    "key": YOUTUBE_API_KEY,
                },
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if items:
                return items[0]["id"]
        except Exception as exc:
            log.error("Failed to resolve handle @%s: %s", username, exc)

    log.warning("Could not resolve channel ID for: %s", channel_input)
    return None


# ── Fetching latest videos ───────────────────

def _get_latest_videos(channel_id: str, max_results: int = 2) -> list[dict]:
    """Return the most recent videos for a channel."""
    try:
        # Step 1: get the uploads playlist ID
        r = http_client.get(
            f"{YT_API}/channels",
            params={
                "part": "contentDetails,snippet",
                "id": channel_id,
                "key": YOUTUBE_API_KEY,
            },
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            log.warning("No channel data for ID: %s", channel_id)
            return []

        channel_name = items[0]["snippet"]["title"]
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # Step 2: get latest items from uploads playlist
        r2 = http_client.get(
            f"{YT_API}/playlistItems",
            params={
                "part": "snippet",
                "playlistId": uploads_playlist,
                "maxResults": max_results,
                "key": YOUTUBE_API_KEY,
            },
        )
        r2.raise_for_status()
        playlist_items = r2.json().get("items", [])

        # Step 3: Check duration (filter out shorts)
        if not playlist_items:
            return []

        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in playlist_items]
        r3 = http_client.get(
            f"{YT_API}/videos",
            params={
                "part": "contentDetails",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY,
            },
        )
        r3.raise_for_status()
        v_items = r3.json().get("items", [])

        # Duration parser
        def parse_yt_duration(ds: str) -> int:
            m = re.match(r'^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', ds)
            if not m: return 0
            return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)

        valid_video_ids = set()
        for v in v_items:
            # We add all videos (including shorts) to be processed
            valid_video_ids.add(v["id"])

        videos = []
        for item in playlist_items:
            snip = item["snippet"]
            video_id = snip["resourceId"]["videoId"]
            if video_id not in valid_video_ids:
                continue

            raw_date = snip.get("publishedAt", "")
            if raw_date:
                try:
                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    tz_ist = timezone(timedelta(hours=5, minutes=30))
                    dt_ist = dt.astimezone(tz_ist)
                    formatted_date = dt_ist.strftime("%d/%m/%Y %I:%M %p GMT +5:30").replace(" AM", " am").replace(" PM", " pm")
                except ValueError:
                    formatted_date = raw_date
            else:
                formatted_date = "Unknown date"

            videos.append({
                "video_id": video_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "title": snip["title"],
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published_at": formatted_date,
            })
        return videos

    except Exception as exc:
        log.error("Error fetching videos for channel %s: %s", channel_id, exc)
        return []


# ── Main check function ──────────────────────

def _process_channel(channel_input: str) -> None:
    channel_id = _resolve_channel_id(channel_input)
    if not channel_id:
        return

    videos = _get_latest_videos(channel_id)

    for video in reversed(videos):   # oldest-first so notifications arrive in order
        vid_id = video["video_id"]
        if is_video_seen(vid_id):
            continue

        log.info("New video found: %s — %s", video["channel_name"], video["title"])
        success = notify_youtube(
            video_title=video["title"],
            published_date=video["published_at"],
            video_url=video["url"],
        )
        if success:
            mark_video_seen(vid_id, channel_id)

def check_youtube_channels() -> None:
    """Check all configured channels for new videos and send Telegram alerts."""
    log.info("Checking %d YouTube channel(s)...", len(YOUTUBE_CHANNELS))

    if not YOUTUBE_CHANNELS:
        return

    with ThreadPoolExecutor(max_workers=min(len(YOUTUBE_CHANNELS), 10)) as executor:
        futures = {executor.submit(_process_channel, ch): ch for ch in YOUTUBE_CHANNELS}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                log.error("Exception in youtube channel worker: %s", e)
