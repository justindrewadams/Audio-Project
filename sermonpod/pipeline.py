"""Glue: discover -> download -> transcribe -> find sermon -> enhance -> feed."""

from __future__ import annotations

import logging
import re

from . import enhance, feed, segment, transcribe, youtube
from .config import Config
from .state import State

log = logging.getLogger(__name__)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "sermon"


def process_video(cfg: Config, state: State, video: dict) -> None:
    vid = video["id"]
    paths = cfg.paths
    raw = youtube.download_audio(cfg.source, vid, paths.audio_dir)
    segments = transcribe.transcribe(raw, paths.transcript_dir / f"{vid}.json",
                                     cfg.trim.whisper_model)

    if "manual_start" in video and "manual_end" in video:
        span = segment.SermonSpan(start=video["manual_start"], end=video["manual_end"], method="manual")
    else:
        span = segment.find_sermon(segments, video, cfg.source.preacher,
                                   cfg.trim.method, cfg.trim.model)
    if span is None:
        state.upsert(vid, status="failed", reason="no sermon found in recording")
        return
    if video["status"] == "unnamed" and span.is_target_preacher is not True:
        who = span.preacher or "unknown speaker"
        state.upsert(vid, status="rejected", reason=f"sermon preached by {who}")
        return
    if span.duration < 8 * 60:
        state.upsert(vid, status="failed",
                     reason=f"detected sermon only {span.duration / 60:.1f} min; set manual_start/end")
        return

    start = max(span.start - cfg.trim.pad_seconds, 0)
    end = span.end + cfg.trim.pad_seconds
    title = span.title or video["title"]
    filename = f"{video['upload_date']}-{_slug(title)}.mp3"
    out = paths.episodes_dir / filename
    enhance.produce_episode(raw, out, start, end, cfg.enhance, tags={
        "title": title,
        "artist": cfg.source.preacher,
        "album": cfg.podcast.title,
        "date": video["upload_date"][:4],
        "genre": "Podcast",
        "comment": video["url"],
    })
    state.upsert(
        vid, status="done", reason=f"trimmed via {span.method}",
        file=f"episodes/{filename}", bytes=out.stat().st_size,
        episode_duration=enhance.probe_duration(out),
        sermon_start=round(start, 2), sermon_end=round(end, 2),
        episode_title=title, scripture=span.scripture, summary=span.summary,
        detected_preacher=span.preacher,
    )
    log.info("done     %s  %s (%.0f min)", vid, title, (end - start) / 60)


def run(cfg: Config, *, discover: bool = True, limit: int | None = None,
        keep_raw: bool = False) -> None:
    state = State(cfg.paths.state_file)
    if discover:
        youtube.discover(cfg.source, state)

    todo_statuses = ["matched"] + (["unnamed"] if cfg.source.check_unnamed_videos else [])
    todo = sorted(state.with_status(*todo_statuses), key=lambda v: v["upload_date"], reverse=True)
    if limit:
        todo = todo[:limit]
    log.info("%d video(s) to process", len(todo))
    for video in todo:
        try:
            process_video(cfg, state, video)
        except Exception as e:
            log.exception("failed on %s", video["id"])
            state.upsert(video["id"], status="failed", reason=str(e)[:500])
        finally:
            raw = cfg.paths.audio_dir / f"{video['id']}.wav"
            if not keep_raw and state.get(video["id"])["status"] in ("done", "rejected"):
                raw.unlink(missing_ok=True)

    write_feed(cfg, state)


def write_feed(cfg: Config, state: State):
    path = feed.build_feed(cfg.podcast, state.with_status("done"), cfg.paths.output_dir)
    log.info("feed written to %s (%d episodes)", path, len(state.with_status("done")))
    return path
