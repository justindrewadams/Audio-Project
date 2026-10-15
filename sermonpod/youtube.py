"""Find candidate videos on the channel and download their audio (via yt-dlp)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yt_dlp

from .config import Source
from .state import State

log = logging.getLogger(__name__)

def _ydl_opts(source: Source, **extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "ignoreerrors": True}
    if source.cookies_file and Path(source.cookies_file).exists():
        opts["cookiefile"] = source.cookies_file
    opts.update(extra)
    return opts


def compile_matcher(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def names_preacher(info: dict, matcher: re.Pattern) -> bool:
    text = " ".join(filter(None, [info.get("title"), info.get("description")]))
    return bool(matcher.search(text))


def list_channel(source: Source) -> list[dict]:
    """Flat listing (id + title) of every video on the configured channel tabs."""
    base = source.channel_url.rstrip("/")
    seen: dict[str, dict] = {}
    with yt_dlp.YoutubeDL(_ydl_opts(source, extract_flat="in_playlist")) as ydl:
        for tab in source.tabs:
            info = ydl.extract_info(f"{base}/{tab}", download=False)
            for entry in (info or {}).get("entries") or []:
                if entry and entry.get("id"):
                    seen.setdefault(entry["id"], entry)
    return list(seen.values())


def video_details(source: Source, video_id: str) -> dict | None:
    with yt_dlp.YoutubeDL(_ydl_opts(source)) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def service_time(info: dict, tz: str) -> tuple[str, str, str]:
    """(local date YYYYMMDD, weekday name, "AM"/"PM") of when the service happened.

    Livestreams carry the stream start time; YouTube's own upload_date is in UTC,
    which puts a Sunday-evening service on Monday."""
    ts = info.get("release_timestamp") or info.get("timestamp")
    if ts:
        local = datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(tz))
        return local.strftime("%Y%m%d"), local.strftime("%A"), "AM" if local.hour < 15 else "PM"
    date = info.get("upload_date") or ""
    if not date:
        return "", "", ""
    return date, datetime.strptime(date, "%Y%m%d").strftime("%A"), ""


def classify(info: dict, source: Source, matcher: re.Pattern) -> tuple[str, str, dict]:
    """Decide what to do with a video from its metadata alone."""
    date, weekday, slot = service_time(info, source.timezone)
    record = {
        "title": info.get("title") or "",
        "description": info.get("description") or "",
        "upload_date": date,
        "weekday": weekday,
        "service": slot,
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
        "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id', '')}",
    }
    excluded = (compile_matcher(source.exclude_patterns).search(record["title"])
                if source.exclude_patterns else None)
    if source.since and date and date < source.since:
        return "rejected", "before since date", record
    if source.days and weekday and weekday not in source.days:
        return "rejected", f"{weekday} service (only {', '.join(source.days)} are included)", record
    if excluded:
        return "rejected", f"title says {excluded.group(0)!r} (excluded)", record
    if record["duration"] and record["duration"] < source.min_minutes * 60:
        return "rejected", "too short", record
    if names_preacher(info, matcher):
        return "matched", "named in title/description", record
    return "unnamed", "preacher not named in metadata", record


def discover(source: Source, state: State) -> list[dict]:
    """Classify new channel videos. Returns the entries newly added to state."""
    matcher = compile_matcher(source.name_patterns)
    added = []
    entries = list_channel(source)
    if not entries:
        raise RuntimeError(f"found no videos at {source.channel_url}. If this keeps happening, "
                           "YouTube is probably blocking this computer; see 'cookies' in the README.")
    for entry in entries:
        vid = entry["id"]
        if state.get(vid):
            continue
        info = video_details(source, vid)
        if not info:
            log.warning("could not read %s (private, members-only, or upcoming)", vid)
            continue
        if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            continue  # come back once the VOD is processed
        status, reason, record = classify(info, source, matcher)
        added.append(state.upsert(vid, status=status, reason=reason, named=status == "matched", **record))
        log.info("%-8s %s  %s", status, vid, record["title"])
    return added


def download_audio(source: Source, video_id: str, dest_dir: Path) -> Path:
    """Download best available audio and convert to 48 kHz WAV for processing."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{video_id}.wav"
    if out.exists():
        return out
    opts = _ydl_opts(
        source,
        ignoreerrors=False,
        format="bestaudio/best",
        outtmpl=str(dest_dir / f"{video_id}.%(ext)s"),
        postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        postprocessor_args={"extractaudio": ["-ar", "48000"]},
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    if not out.exists():
        raise RuntimeError(f"download of {video_id} produced no WAV")
    return out
