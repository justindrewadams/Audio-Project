"""Glue: discover -> download -> transcribe -> find sermon -> enhance -> feed."""

from __future__ import annotations

import logging
import re

from . import enhance, feed, segment, site, transcribe, voice, youtube
from .config import Config
from .state import State

log = logging.getLogger(__name__)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "sermon"


def decide(*, named: bool, forced: bool, claude: str, voice_match: str,
           unknown_policy: str) -> tuple[str, str]:
    """Combine the evidence on who preached. Returns (verdict, reason) where
    verdict is "accept", "reject" or "review" (held for a human to look at).

    named:       the video title/description names him
    forced:      added by hand with `sermonpod add`
    claude:      what the service itself says ("yes" | "no" | "unknown")
    voice_match: what his voiceprint says ("yes" | "no" | "unknown")
    """
    if forced:
        return "accept", "added manually"
    if voice_match == "yes":
        if claude == "no":
            return "review", "voice matches him but the service introduces someone else"
        return "accept", "voice matches him"
    if voice_match == "no":
        if named or claude == "yes":
            return "review", "named as preacher but the voice doesn't match him"
        return "reject", "voice doesn't match him"
    if named:
        return "accept", "named in title/description"
    if claude == "yes":
        return "accept", "identified in the service"
    if claude == "no":
        return "reject", "someone else preached"
    if unknown_policy == "include":
        return "accept", "preacher not identified; included by default"
    return "reject", "could not tell who preached; use `sermonpod add` if it was him"


_embedder: voice.Embedder | None = None


def _voice_check(cfg: Config, vid: str, raw, segments, span) -> tuple[voice.Profile, float | None, str]:
    global _embedder
    if _embedder is None:
        _embedder = voice.Embedder(cfg.voice, cfg.paths.work_dir / "models")
    profile = voice.Profile(cfg.paths.voice_dir, cfg.voice)
    emb = voice.embed_service(_embedder, raw, segments, span.start, span.end, cfg.voice)
    profile.store(vid, emb)
    score = profile.score(emb, exclude=vid)
    return profile, score, profile.decide(score)


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
    if span.duration <= 0:
        reason = {"bible_class": "recording is a Bible class",
                  "other": "recording is not a worship service"}.get(span.recording, "no sermon in this service")
        state.upsert(vid, status="rejected", reason=reason)
        return

    profile, score, voice_match = None, None, "unknown"
    if cfg.voice.enabled:
        profile, score, voice_match = _voice_check(cfg, vid, raw, segments, span)
    named = bool(video.get("named"))
    verdict, why = decide(
        named=named,
        forced=bool(video.get("forced")),
        claude=span.preacher_match, voice_match=voice_match,
        unknown_policy=cfg.source.unknown_preacher,
    )
    voice_fields = {"voice_score": None if score is None else round(score, 3), "voice_match": voice_match,
                    "claude_match": span.preacher_match}
    if verdict != "accept":
        if verdict == "reject" and profile and (voice_match == "no" or span.preacher_match == "no"):
            profile.add_impostor(vid)
        if span.preacher and verdict == "reject":
            why += f" ({span.preacher})"
        state.upsert(vid, status="rejected" if verdict == "reject" else "review", reason=why,
                     detected_preacher=span.preacher, **voice_fields)
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
        detected_preacher=span.preacher, decision=why, **voice_fields,
    )
    confirmed = (video.get("forced") or named or span.preacher_match == "yes" or voice_match == "yes")
    if profile and cfg.voice.auto_enroll and confirmed and voice_match != "no":
        profile.enroll(vid)
    log.info("done     %s  %s (%.0f min, %s)", vid, title, (end - start) / 60, why)


# ---------------------------------------------------------------- voice profile upkeep

def bootstrap_profile(cfg: Config, state: State) -> list[str]:
    """Start the profile from the voice that preaches most services."""
    profile = voice.Profile(cfg.paths.voice_dir, cfg.voice)
    candidates = [v["id"] for v in state.with_status("done", "review")
                  if v["id"] not in profile.impostors]
    group = voice.find_regular_voice(profile, candidates, cfg.voice.bootstrap_similarity)
    for vid in group:
        profile.enroll(vid)
    return group


def recheck(cfg: Config, state: State) -> list[tuple[str, str, str]]:
    """Re-score every processed video against the current profile and update
    decisions that change. Returns (video_id, old_status, new_status)."""
    profile = voice.Profile(cfg.paths.voice_dir, cfg.voice)
    if not profile.ready:
        return []
    changes = []
    for v in state.with_status("done", "rejected", "review"):
        vid = v["id"]
        if v.get("forced") or v.get("confirmed") is not None or not profile.has_embeddings(vid):
            continue
        score = profile.score(profile.embeddings(vid), exclude=vid)
        vm = profile.decide(score)
        verdict, why = decide(named=bool(v.get("named")), forced=False,
                              claude=v.get("claude_match", "unknown"), voice_match=vm,
                              unknown_policy=cfg.source.unknown_preacher)
        old = v["status"]
        if verdict == "accept":
            new = "done" if v.get("file") else "matched"   # matched = produce it on the next run
        elif verdict == "reject" and old == "done":
            new = "review"   # never silently drop a published episode
        else:
            new = {"reject": "rejected", "review": "review"}[verdict]
        state.upsert(vid, voice_score=None if score is None else round(score, 3), voice_match=vm)
        if new != old:
            state.upsert(vid, status=new, reason=why)
            changes.append((vid, old, new))
    return changes


def confirm(cfg: Config, state: State, video_ids: list[str], is_him: bool) -> None:
    """A person says these videos are (or aren't) his sermons."""
    profile = voice.Profile(cfg.paths.voice_dir, cfg.voice)
    for vid in video_ids:
        v = state.get(vid)
        if v is None:
            raise SystemExit(f"unknown video {vid}")
        if is_him:
            if profile.has_embeddings(vid):
                profile.enroll(vid)
            if v.get("file"):  # already produced and published
                state.upsert(vid, status="done", reason="confirmed as his", confirmed=True)
            else:
                state.upsert(vid, status="matched", reason="confirmed as his", confirmed=True, forced=True)
        else:
            if profile.has_embeddings(vid):
                profile.remove(vid)
                profile.add_impostor(vid)
            state.upsert(vid, status="rejected", reason="confirmed not his", confirmed=False)


def run(cfg: Config, *, discover: bool = True, limit: int | None = None,
        keep_raw: bool = False, scan: int | None = None) -> None:
    state = State(cfg.paths.state_file)
    if discover:
        youtube.discover(cfg.source, state, newest=scan)

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

    if cfg.voice.enabled and cfg.voice.auto_enroll:
        if not voice.Profile(cfg.paths.voice_dir, cfg.voice).ready:
            group = bootstrap_profile(cfg, state)
            if group:
                log.info("voice profile started from %d sermons that share one voice", len(group))
        for vid, old, new in recheck(cfg, state):
            log.info("voice recheck: %s %s -> %s", vid, old, new)

    write_feed(cfg, state)


def write_feed(cfg: Config, state: State):
    path = feed.build_feed(cfg.podcast, state.with_status("done"), cfg.paths.output_dir)
    site.build_site(cfg.podcast, list(state.videos.values()), cfg.paths.output_dir, cfg.podcast.manage_url)
    log.info("feed written to %s (%d episodes)", path, len(state.with_status("done")))
    return path
