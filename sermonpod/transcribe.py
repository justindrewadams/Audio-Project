"""Speech-to-text with timestamps (faster-whisper), cached as JSON."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_model_cache: dict[str, object] = {}


def _model(name: str):
    if name not in _model_cache:
        from faster_whisper import WhisperModel  # heavy import, load lazily

        _model_cache[name] = WhisperModel(name, device="auto", compute_type="auto")
    return _model_cache[name]


def transcribe(audio: Path, out_json: Path, model_name: str) -> list[dict]:
    """Return [{start, end, text}, ...] segments, reusing the cache if present."""
    if out_json.exists():
        return json.loads(out_json.read_text())
    log.info("transcribing %s with whisper %s (this takes a while)", audio.name, model_name)
    segments, _ = _model(model_name).transcribe(
        str(audio),
        language="en",
        vad_filter=True,
        condition_on_previous_text=False,  # avoids hallucination loops during songs
    )
    result = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segments
    ]
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=1))
    return result
