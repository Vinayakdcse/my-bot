"""
youtube_checker.py - Poll YouTube Data API v3 for new uploads.

Video delivery strategy:
  1. yt-dlp downloads the best quality stream <= 45 MB
  2. If download succeeds  → sendVideo (plays natively in Telegram)
  3. If too large / fails  → sendPhoto (thumbnail) + Watch on YouTube link
"""

import io
import logging
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    YOUTUBE_API_KEY,
    YOUTUBE_CHANNELS,
    YT_MAX_VIDEO_BYTES,
    YT_FALLBACK_TO_THUMBNAIL,
)
from database import is_video_seen, mark_video_seen
from telegram_notifier import notify_youtube
from client import http_client

log = logging.getLogger(__name__)

YT_API      = "https://www.googleapis.com/youtube/v3"
_THUMB_KEYS = ["maxres", "standard", "high", "medium", "default"]


# ── yt-dlp availability check ────────────────────────────────────────────────

def _yt_dlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


# ── Channel ID resolution ────────────────────────────────────────────────────

def _resolve_channel_id(channel_input: str) -> str | None:
    if re.match(r"^UC[\w-]{22}$", channel_input):
        return channel_input
    m = re.search(r"/channel/(UC[\w-]{22})", channel_input)
    if m:
        return m.group(1)
    handle = re.search(r"@([\w.-]+)", channel_input)
    if handle:
        username = handle.group(1)
        try:
            r = http_client.get(
                f"{YT_API}/channels",
                params={"part": "id", "forHandle": f"@{username}", "key": YOUTUBE_API_KEY},
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if items:
                return items[0]["id"]
        except Exception as exc:
            log.error("Failed to resolve handle @%s: %s", username, exc)
    log.warning("Could not resolve channel ID for: %s", channel_input)
    return None


# ── Thumbnail downloader ─────────────────────────────────────────────────────

def _fetch_thumbnail(thumbnails: dict) -> tuple[bytes | None, str | None]:
    for key in _THUMB_KEYS:
        entry = thumbnails.get(key)
        if entry and entry.get("url"):
            url = entry["url"]
            try:
                r = http_client.get(url, timeout=15)
                r.raise_for_status()
                return r.content, url
            except Exception as exc:
                log.debug("Thumbnail download failed (%s): %s", key, exc)
    return None, None


# ── Duration helpers ─────────────────────────────────────────────────────────

def _parse_yt_duration(ds: str) -> int:
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", ds)
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def _is_short(duration_seconds: int) -> bool:
    return duration_seconds <= 60


# ── yt-dlp video download ────────────────────────────────────────────────────

def _download_video_ytdlp(video_url: str, max_bytes: int) -> bytes | None:
    """
    Download a YouTube video using yt-dlp into memory.
    Selects the best format that fits within max_bytes.
    Returns raw bytes or None if unavailable / too large / yt-dlp not installed.
    """
    if not _yt_dlp_available():
        log.debug("yt-dlp not installed, skipping video download.")
        return None

    try:
        import yt_dlp

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "video.mp4")

            ydl_opts = {
                "format": (
                    # Best combined stream under ~45 MB
                    # filesize_approx keeps it fast (no full manifest scan)
                    f"bestvideo[ext=mp4][filesize<{max_bytes}]+bestaudio[ext=m4a]"
                    f"/best[ext=mp4][filesize<{max_bytes}]"
                    f"/best[filesize<{max_bytes}]"
                ),
                "outtmpl":        out_path,
                "quiet":          True,
                "no_warnings":    True,
                "noplaylist":     True,
                "socket_timeout": 30,
                # Merge into mp4 if separate streams
                "merge_output_format": "mp4",
                # Avoid writing cookies / cache on server
                "cookiefile":     None,
                "cachedir":       False,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                # Check estimated size before downloading
                est_size = info.get("filesize") or info.get("filesize_approx") or 0
                if est_size and est_size > max_bytes:
                    log.info(
                        "Video estimated %.1f MB > limit %.1f MB, skipping download.",
                        est_size / 1024 / 1024,
                        max_bytes / 1024 / 1024,
                    )
                    return None

                ydl.download([video_url])

            # Find downloaded file (yt-dlp may rename it)
            for fname in os.listdir(tmpdir):
                fpath = os.path.join(tmpdir, fname)
                size  = os.path.getsize(fpath)
                if size > max_bytes:
                    log.info(
                        "Downloaded file %.1f MB exceeds limit, skipping upload.",
                        size / 1024 / 1024,
                    )
                    return None
                with open(fpath, "rb") as f:
                    data = f.read()
                log.info("yt-dlp downloaded %.1f MB for %s", size / 1024 / 1024, video_url)
                return data

    except Exception as exc:
        log.warning("yt-dlp download failed for %s: %s", video_url, exc)

    return None


# ── Fetch latest videos from API ─────────────────────────────────────────────

def _get_latest_videos(channel_id: str, max_results: int = 2) -> list[dict]:
    try:
        r = http_client.get(
            f"{YT_API}/channels",
            params={"part": "contentDetails,snippet", "id": channel_id, "key": YOUTUBE_API_KEY},
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            log.warning("No channel data for ID: %s", channel_id)
            return []

        channel_name     = items[0]["snippet"]["title"]
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        r2 = http_client.get(
            f"{YT_API}/playlistItems",
            params={
                "part":       "snippet",
                "playlistId": uploads_playlist,
                "maxResults": max_results,
                "key":        YOUTUBE_API_KEY,
            },
        )
        r2.raise_for_status()
        playlist_items = r2.json().get("items", [])
        if not playlist_items:
            return []

        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in playlist_items]
        r3 = http_client.get(
            f"{YT_API}/videos",
            params={"part": "contentDetails,snippet", "id": ",".join(video_ids), "key": YOUTUBE_API_KEY},
        )
        r3.raise_for_status()
        video_details = {v["id"]: v for v in r3.json().get("items", [])}

        videos = []
        for item in playlist_items:
            snip     = item["snippet"]
            video_id = snip["resourceId"]["videoId"]
            detail   = video_details.get(video_id, {})

            duration_str = detail.get("contentDetails", {}).get("duration", "PT0S")
            duration_sec = _parse_yt_duration(duration_str)
            is_short     = _is_short(duration_sec)

            url = (
                f"https://www.youtube.com/shorts/{video_id}"
                if is_short
                else f"https://www.youtube.com/watch?v={video_id}"
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
                formatted_date = raw_date or "Unknown date"

            thumbnails  = (
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
                "duration_sec": duration_sec,
            })

        return videos

    except Exception as exc:
        log.error("Error fetching videos for channel %s: %s", channel_id, exc)
        return []


# ── Per-channel processor ────────────────────────────────────────────────────

def _process_channel(channel_input: str) -> None:
    channel_id = _resolve_channel_id(channel_input)
    if not channel_id:
        return

    videos = _get_latest_videos(channel_id)

    for video in reversed(videos):
        vid_id = video["video_id"]
        if is_video_seen(vid_id):
            continue

        kind = "Short" if video["is_short"] else "Video"
        log.info("New %s: %s — %s", kind, video["channel_name"], video["title"])

        # Try to download video for direct playback in Telegram
        video_bytes = _download_video_ytdlp(video["url"], YT_MAX_VIDEO_BYTES)

        success = notify_youtube(
            video_title     = video["title"],
            published_date  = video["published_at"],
            video_url       = video["url"],
            channel_name    = video["channel_name"],
            thumbnail_bytes = video["thumb_bytes"],
            thumbnail_url   = video["thumb_url"],
            video_bytes     = video_bytes,        # None → falls back to thumbnail
        )
        if success:
            mark_video_seen(vid_id, channel_id)


# ── Public entry point ───────────────────────────────────────────────────────

def check_youtube_channels() -> None:
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
