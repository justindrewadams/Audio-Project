"""Locate the sermon inside a full worship-service recording.

Two strategies:
  * claude    - Claude reads the timestamped transcript and returns the sermon's
                first and last lines, plus title/scripture/summary for show notes.
  * heuristic - speaking-rate analysis. Congregational singing transcribes at a
                much lower words-per-minute than preaching, so the longest stretch
                of sustained speech-rate text is almost always the sermon.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class SermonSpan:
    start: float
    end: float
    preacher: str = ""
    preacher_match: str = "unknown"  # "yes" | "no" | "unknown"
    recording: str = "worship_service"  # "worship_service" | "bible_class" | "other"
    title: str = ""
    scripture: list[str] = field(default_factory=list)
    summary: str = ""
    method: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


# ---------------------------------------------------------------- heuristic

def heuristic_span(segments: list[dict], bin_seconds: float = 30.0,
                   min_wpm: float = 105.0, max_gap_bins: int = 2) -> SermonSpan | None:
    if not segments:
        return None
    total = max(s["end"] for s in segments)
    nbins = int(total // bin_seconds) + 1
    words = [0.0] * nbins
    for s in segments:
        n = len(s["text"].split())
        dur = max(s["end"] - s["start"], 0.01)
        # spread the segment's words across the bins it overlaps
        b = int(s["start"] // bin_seconds)
        while b < nbins and b * bin_seconds < s["end"]:
            lo = max(s["start"], b * bin_seconds)
            hi = min(s["end"], (b + 1) * bin_seconds)
            words[b] += n * max(hi - lo, 0) / dur
            b += 1
    talking = [w * 60.0 / bin_seconds >= min_wpm for w in words]

    best = (0, -1)  # inclusive bin range
    start, gap = None, 0
    for i, t in enumerate(talking + [False] * (max_gap_bins + 1)):
        if t:
            if start is None:
                start = i
            gap, last = 0, i
        elif start is not None:
            gap += 1
            if gap > max_gap_bins:
                if last - start > best[1] - best[0]:
                    best = (start, last)
                start, gap = None, 0
    if best[1] < best[0]:
        return None
    lo_t, hi_t = best[0] * bin_seconds, (best[1] + 1) * bin_seconds
    inside = [s for s in segments if s["end"] > lo_t and s["start"] < hi_t]
    return SermonSpan(start=inside[0]["start"], end=inside[-1]["end"], method="heuristic")


# ---------------------------------------------------------------- claude

SYSTEM_PROMPT = """\
You analyze transcripts of Sunday worship services from a church of Christ \
congregation. Services usually contain a cappella congregational singing, \
prayers, scripture reading, the Lord's Supper, announcements, a sermon, and an \
invitation song. The transcript was machine-generated, so song lyrics may be \
garbled and names misspelled.

The congregation also holds Bible classes (Sunday morning before worship, and \
Wednesday night). Classes are not sermons: a teacher works through a text, often \
takes questions or comments from the class, and there is usually no Lord's Supper \
or invitation. A recording may be only a class, only a worship service, or a \
class followed by the worship service. Report what the recording is, and never \
choose the class lesson as the sermon; if the recording contains no worship \
service, set sermon_found to false.

Find the sermon: the main lesson delivered by one preacher in the worship service. It starts where the \
preacher begins his lesson (include his own opening remarks and his reading of \
the sermon text, but not a separate scripture reading by someone else or the \
song before it). It ends with his closing words and invitation appeal, before \
the invitation song begins or another person takes over.

Every transcript line starts with its timestamp in seconds, like [1234.5]. \
Report sermon boundaries by copying the timestamps of the first and last lines \
of the sermon exactly as they appear.

Identify the preacher from introductions ("our speaker this morning is..."), \
self-references ("when I was growing up in..."), people addressing him by name, \
or the video metadata. Set preacher_match to "yes" when the evidence points to \
the target preacher named in the request, "no" when it points to someone else \
(for example a guest speaker who is introduced), and "unknown" when nothing in \
the service says who preached. Leave preacher empty if you cannot tell.

For the show notes, give a concise sermon title (use the one the preacher or \
video gives, if any), the main scripture references, and a 2-3 sentence summary \
written for podcast listeners."""

SCHEMA = {
    "type": "object",
    "properties": {
        "recording": {"type": "string", "enum": ["worship_service", "bible_class",
                                                    "class_then_worship", "other"]},
        "sermon_found": {"type": "boolean"},
        "first_line_timestamp": {"type": "number"},
        "last_line_timestamp": {"type": "number"},
        "preacher": {"type": "string"},
        "preacher_match": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "title": {"type": "string"},
        "scripture": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "recording", "sermon_found", "first_line_timestamp", "last_line_timestamp", "preacher",
        "preacher_match", "title", "scripture", "summary",
    ],
    "additionalProperties": False,
}


def format_transcript(segments: list[dict]) -> str:
    return "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in segments)


def claude_span(segments: list[dict], video: dict, target_preacher: str, model: str,
                client=None) -> SermonSpan | None:
    import anthropic

    client = client or anthropic.Anthropic()
    user = (
        f"Target preacher: {target_preacher}\n"
        f"Video title: {video.get('title', '')}\n"
        f"Upload date: {video.get('upload_date', '')}\n"
        f"Video description:\n{video.get('description', '')[:2000]}\n\n"
        f"<transcript>\n{format_transcript(segments)}\n</transcript>"
    )
    with client.beta.messages.stream(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": user}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        log.warning("Claude declined to analyze %s", video.get("id"))
        return None
    if response.stop_reason == "max_tokens":
        log.warning("Claude ran out of tokens on %s", video.get("id"))
        return None
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    if data.get("recording") == "bible_class" or not data["sermon_found"]:
        return SermonSpan(start=0, end=0, recording=data.get("recording", "other"), method="claude")
    return snap_to_segments(segments, data)


def snap_to_segments(segments: list[dict], data: dict) -> SermonSpan | None:
    """Map Claude's line timestamps to real segment boundaries."""
    def nearest(t: float) -> dict:
        return min(segments, key=lambda s: abs(s["start"] - t))

    first = nearest(float(data["first_line_timestamp"]))
    last = nearest(float(data["last_line_timestamp"]))
    if last["end"] <= first["start"]:
        return None
    return SermonSpan(
        start=first["start"],
        end=last["end"],
        preacher=data.get("preacher", ""),
        preacher_match=data.get("preacher_match", "unknown"),
        recording="worship_service",
        title=data.get("title", ""),
        scripture=list(data.get("scripture", [])),
        summary=data.get("summary", ""),
        method="claude",
    )


def find_sermon(segments: list[dict], video: dict, target_preacher: str, method: str,
                model: str) -> SermonSpan | None:
    if method == "claude":
        try:
            span = claude_span(segments, video, target_preacher, model)
            if span is None:
                log.warning("Claude could not analyze %s", video.get("id"))
            return span
        except Exception as e:  # missing key, network, bad JSON: fall back
            log.warning("Claude analysis failed for %s (%s); using heuristic", video.get("id"), e)
    return heuristic_span(segments)
