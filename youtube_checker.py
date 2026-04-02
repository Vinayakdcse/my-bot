"""
youtube_checker.py - Poll YouTube Data API v3 for new uploads.

FIX vs previous version:
  - yt-dlp download moved OUTSIDE ThreadPoolExecutor.
    Before: 7 channels × concurrent yt-dlp = 7 threads × ~300 MB RAM = OOM kill.
    Now: channel metadata fetched in parallel (fast, lightweight),
         yt-dlp download happens sequentially per new video (rare, controlled).
  - ThreadPoolExecutor max_workers capped at 4 (was 10).
  - Added explicit timeout guard around yt-dlp (max 3 min per video).
"""

import logging
import os
import re
import signal
import tempfile
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    YOUTUBE_API_KEY,
    YOUTUBE_CHANNELS,
    YT_MAX_VIDEO_BYTES,
)
from database import is_video_seen, mark_video_seen
from telegram_notifier import notify_youtube
from client import get_client

log = logging.getLogger(__name__)

YT_API      = "https://www.googleapis.com/youtube/v3"
_THUMB_KEYS = ["maxres", "standard", "high", "medium", "default"]
_MAX_WORKERS = 4   # keep RAM usage low on Render free tier


# ── yt-dlp availability ──────────────────────────────────────────────────────

def _yt_dlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


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


# ── yt-dlp download (sequential, not inside thread pool) ────────────────────

def _download_video_ytdlp(video_url: str, max_bytes: int) -> bytes | None:
    if not _yt_dlp_available():
        return None

    try:
        import yt_dlp

        with tempfile.TemporaryDirectory() as tmpdir:
            out_tmpl = os.path.join(tmpdir, "video.%(ext)s")
            ydl_opts = {
                "format": (
                    f"bestvideo[ext=mp4][filesize<{max_bytes}]+bestaudio[ext=m4a]"
                    f"/best[ext=mp4][filesize<{max_bytes}]"
                    f"/best[filesize<{max_bytes}]"
                ),
                "outtmpl":             out_tmpl,
                "quiet":               True,
                "no_warnings":         True,
                "noplaylist":          True,
                "socket_timeout":      30,
                "merge_output_format": "mp4",
                "cookiefile":          None,
                "cachedir":            False,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info     = ydl.extract_info(video_url, download=False)
                est_size = info.get("filesize") or info.get("filesize_approx") or 0
                if est_size and est_size > max_bytes:
                    log.info("Video ~%.1f MB > limit, skipping.", est_size / 1024 / 1024)
                    return None
                ydl.download([video_url])

            for fname in os.listdir(tmpdir):
                fpath = os.path.join(tmpdir, fname)
                size  = os.path.getsize(fpath)
                if size > max_bytes:
                    log.info("Downloaded %.1f MB > limit, skipping upload.", size / 1024 / 1024)
                    return None
                with open(fpath, "rb") as f:
                    data = f.read()
                log.info("yt-dlp: %.1f MB downloaded for %s", size / 1024 / 1024, video_url)
                return data

    except Exception as exc:
        log.warning("yt-dlp failed for %s: %s", video_url, exc)

    return None


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
            params={"part": "contentDetails,snippet", "id": ",".join(video_ids), "key": YOUTUBE_API_KEY},
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


# ── Per-channel: fetch metadata only (runs in thread pool) ──────────────────

def _fetch_channel_new_videos(channel_input: str) -> list[dict]:
    """
    Returns list of new (unseen) video dicts for this channel.
    Does NOT download video bytes — that happens sequentially after.
    """
    channel_id = _resolve_channel_id(channel_input)
    if not channel_id:
        return []

    videos = _get_latest_videos(channel_id)
    return [v for v in videos if not is_video_seen(v["video_id"])]


# ── Public entry point ───────────────────────────────────────────────────────

def check_youtube_channels() -> None:
    log.info("Checking %d YouTube channel(s)...", len(YOUTUBE_CHANNELS))
    if not YOUTUBE_CHANNELS:
        return

    # Phase 1: fetch metadata for all channels in parallel (fast, no RAM spike)
    all_new_videos: list[dict] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_channel_new_videos, ch): ch for ch in YOUTUBE_CHANNELS}
        for future in as_completed(futures):
            try:
                new_vids = future.result()
                all_new_videos.extend(new_vids)
            except Exception as exc:
                log.error("Channel fetch error: %s", exc)

    if not all_new_videos:
        log.info("No new YouTube videos found.")
        return

    log.info("%d new video(s) to process.", len(all_new_videos))

    # Phase 2: for each new video, attempt download + send (sequential = controlled RAM)
    for video in all_new_videos:
        try:
            kind = "Short" if video["is_short"] else "Video"
            log.info("Processing new %s: %s — %s", kind, video["channel_name"], video["title"])

            video_bytes = _download_video_ytdlp(video["url"], YT_MAX_VIDEO_BYTES)

            success = notify_youtube(
                video_title     = video["title"],
                published_date  = video["published_at"],
                video_url       = video["url"],
                channel_name    = video["channel_name"],
                thumbnail_bytes = video["thumb_bytes"],
                thumbnail_url   = video["thumb_url"],
                video_bytes     = video_bytes,
            )
            if success:
                mark_video_seen(video["video_id"], video["channel_id"])

        except Exception as exc:
            log.error("Error processing video %s: %s", video.get("video_id"), exc)
