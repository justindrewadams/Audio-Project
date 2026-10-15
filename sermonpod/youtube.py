"""Find candidate videos on the channel and download their audio (via yt-dlp)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yt_dlp

from .config import Source
from .state import State

log = logging.getLogger(__name__)

TABS = ("videos", "streams")


def _ydl_opts(source: Source, **extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "ignoreerrors": True}
    if source.cookies_file:
        opts["cookiefile"] = source.cookies_file
    opts.update(extra)
    return opts


def compile_matcher(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def names_preacher(info: dict, matcher: re.Pattern) -> bool:
    text = " ".join(filter(None, [info.get("title"), info.get("description")]))
    return bool(matcher.search(text))


def list_channel(source: Source) -> list[dict]:
    """Flat listing (id + title) of every upload and past livestream."""
    base = source.channel_url.rstrip("/")
    seen: dict[str, dict] = {}
    with yt_dlp.YoutubeDL(_ydl_opts(source, extract_flat="in_playlist")) as ydl:
        for tab in TABS:
            info = ydl.extract_info(f"{base}/{tab}", download=False)
            for entry in (info or {}).get("entries") or []:
                if entry and entry.get("id"):
                    seen.setdefault(entry["id"], entry)
    return list(seen.values())


def video_details(source: Source, video_id: str) -> dict | None:
    with yt_dlp.YoutubeDL(_ydl_opts(source)) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def discover(source: Source, state: State) -> list[dict]:
    """Classify new channel videos. Returns the entries newly added to state."""
    matcher = compile_matcher(source.name_patterns)
    added = []
    for entry in list_channel(source):
        vid = entry["id"]
        if state.get(vid):
            continue
        info = video_details(source, vid)
        if not info:
            log.warning("could not read %s (private, members-only, or upcoming)", vid)
            continue
        if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            continue  # come back once the VOD is processed
        record = {
            "title": info.get("title") or "",
            "description": info.get("description") or "",
            "upload_date": info.get("upload_date") or "",
            "duration": info.get("duration") or 0,
            "thumbnail": info.get("thumbnail") or "",
            "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
        }
        if source.since and record["upload_date"] and record["upload_date"] < source.since:
            status, reason = "rejected", "before since date"
        elif record["duration"] and record["duration"] < source.min_minutes * 60:
            status, reason = "rejected", "too short"
        elif names_preacher(info, matcher):
            status, reason = "matched", "named in title/description"
        else:
            status, reason = "unnamed", "preacher not named in metadata"
        added.append(state.upsert(vid, status=status, reason=reason, **record))
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
