"""Voiceprint: recognize the preacher by the sound of his voice.

Each processed service gets a set of speaker embeddings (one 256-number vector
per few seconds of audio, from a neural speaker-recognition model):
  * "sermon": windows from inside the detected sermon
  * "other":  windows of ordinary speech elsewhere in the service (prayers,
              scripture readings, announcements), mostly other men

The profile is the average of his enrolled sermons. A new sermon is scored by
the median cosine similarity of its windows to that average (1.0 = identical
voice, ~0.3 = unrelated speakers). The cutoff is calibrated from the data: it
sits between how well his own sermons match the profile (leave-one-out) and how
well the other voices in the same building match it.

The profile keeps learning: every sermon confirmed as his is enrolled, up to
`max_sermons` most recent, so it follows changes in microphone or room sound.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import Voice

log = logging.getLogger(__name__)

SR = 16000


# ---------------------------------------------------------------- embedding model

class Embedder:
    """Wraps a sherpa-onnx speaker-embedding model (downloaded on first use)."""

    def __init__(self, settings: Voice, model_dir: Path):
        self.settings = settings
        self.model_path = model_dir / Path(settings.model_url).name
        self._extractor = None

    def _load(self):
        if self._extractor is None:
            import sherpa_onnx

            if not self.model_path.exists():
                self.model_path.parent.mkdir(parents=True, exist_ok=True)
                log.info("downloading speaker model to %s", self.model_path)
                tmp = self.model_path.with_suffix(".part")
                urllib.request.urlretrieve(self.settings.model_url, tmp)
                tmp.rename(self.model_path)
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.model_path), num_threads=2)
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        return self._extractor

    def embed(self, samples: np.ndarray) -> np.ndarray:
        ex = self._load()
        stream = ex.create_stream()
        stream.accept_waveform(sample_rate=SR, waveform=samples.astype(np.float32))
        stream.input_finished()
        v = np.asarray(ex.compute(stream), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


def load_clip(audio: Path, start: float, length: float) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(audio),
         "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True)
    return np.frombuffer(proc.stdout, dtype="<f4")


# ---------------------------------------------------------------- window selection

def _spread(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    idx = np.linspace(0, len(items) - 1, n).round().astype(int)
    return [items[i] for i in idx]


def sermon_windows(segments: list[dict], start: float, end: float, n: int, length: float) -> list[float]:
    """Start times of up to n windows of speech inside the sermon (skipping its first minute,
    which can include someone else finishing a reading or introducing him)."""
    lo = start + min(60.0, (end - start) * 0.1)
    starts = [s["start"] for s in segments if lo <= s["start"] <= end - length]
    return _spread(starts, n)


def other_windows(segments: list[dict], start: float, end: float, n: int,
                  min_words_per_sec: float = 1.8) -> list[tuple[float, float]]:
    """Spoken (not sung) segments outside the sermon: (start, length) pairs."""
    picks = []
    for s in segments:
        dur = s["end"] - s["start"]
        outside = s["end"] < start - 5 or s["start"] > end + 5
        if outside and dur >= 3 and len(s["text"].split()) / dur >= min_words_per_sec:
            picks.append((s["start"], min(dur, 6.0)))
    return _spread(picks, n)


@dataclass
class ServiceEmbeddings:
    sermon: np.ndarray   # (k, dim)
    other: np.ndarray    # (m, dim)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, sermon=self.sermon, other=self.other)

    @classmethod
    def load(cls, path: Path) -> "ServiceEmbeddings":
        d = np.load(path)
        return cls(sermon=d["sermon"], other=d["other"])


def embed_service(embedder: Embedder, audio: Path, segments: list[dict], start: float, end: float,
                  settings: Voice) -> ServiceEmbeddings:
    def run(windows):
        out = []
        for t, length in windows:
            clip = load_clip(audio, t, length)
            if clip.size < SR * 2 or np.sqrt(np.mean(clip ** 2)) < 10 ** (-45 / 20):
                continue  # too short or near-silent
            out.append(embedder.embed(clip))
        return np.array(out, dtype=np.float32) if out else np.empty((0, 0), dtype=np.float32)

    sermon = run([(t, settings.window_seconds)
                  for t in sermon_windows(segments, start, end, settings.windows_per_sermon,
                                          settings.window_seconds)])
    other = run(other_windows(segments, start, end, settings.other_windows))
    return ServiceEmbeddings(sermon=sermon, other=other)



# ---------------------------------------------------------------- profile

def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


class Profile:
    """The preacher's voice profile, stored as a list of enrolled video ids plus
    each video's embeddings in <voice_dir>/<video_id>.npz."""

    def __init__(self, voice_dir: Path, settings: Voice):
        self.dir = Path(voice_dir)
        self.settings = settings
        self.file = self.dir / "profile.json"
        data = json.loads(self.file.read_text()) if self.file.exists() else {}
        self.enrolled: list[str] = data.get("enrolled", [])
        self.impostors: list[str] = data.get("impostors", [])
        self.stats: dict = data.get("stats", {})
        self._cache: dict[str, ServiceEmbeddings] = {}

    # -- storage
    def emb_path(self, vid: str) -> Path:
        return self.dir / f"{vid}.npz"

    def has_embeddings(self, vid: str) -> bool:
        return self.emb_path(vid).exists()

    def embeddings(self, vid: str) -> ServiceEmbeddings:
        if vid not in self._cache:
            self._cache[vid] = ServiceEmbeddings.load(self.emb_path(vid))
        return self._cache[vid]

    def store(self, vid: str, emb: ServiceEmbeddings) -> None:
        emb.save(self.emb_path(vid))
        self._cache[vid] = emb

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(
            {"enrolled": self.enrolled, "impostors": self.impostors, "stats": self.stats}, indent=2))

    # -- model
    @property
    def ready(self) -> bool:
        return len(self.enrolled) >= self.settings.min_sermons

    def centroid(self, exclude: str | None = None) -> np.ndarray:
        # each sermon counts equally, however many windows it produced
        means = [_unit(self.embeddings(v).sermon.mean(axis=0))
                 for v in self.enrolled if v != exclude and len(self.embeddings(v).sermon)]
        return _unit(np.mean(means, axis=0))

    @staticmethod
    def _score(windows: np.ndarray, centroid: np.ndarray) -> float:
        return float(np.median(windows @ centroid))

    def score(self, emb: ServiceEmbeddings, exclude: str | None = None) -> float | None:
        if not len(emb.sermon) or not self.ready:
            return None
        return self._score(emb.sermon, self.centroid(exclude=exclude))

    @property
    def threshold(self) -> float:
        if self.settings.threshold:
            return self.settings.threshold
        return self.stats.get("threshold", 0.5)

    def decide(self, score: float | None) -> str:
        if score is None:
            return "unknown"
        if score >= self.threshold:
            return "yes"
        if score < self.threshold - self.settings.margin:
            return "no"
        return "unknown"

    # -- learning
    def enroll(self, vid: str) -> None:
        if vid in self.impostors:
            self.impostors.remove(vid)
        if vid not in self.enrolled:
            self.enrolled.append(vid)
        self.enrolled = self.enrolled[-self.settings.max_sermons:]
        self.calibrate()

    def add_impostor(self, vid: str) -> None:
        if vid not in self.enrolled and vid not in self.impostors:
            self.impostors.append(vid)
            self.impostors = self.impostors[-self.settings.max_sermons:]
            self.calibrate()

    def remove(self, vid: str) -> None:
        for lst in (self.enrolled, self.impostors):
            if vid in lst:
                lst.remove(vid)
        self.calibrate()

    def calibrate(self) -> None:
        """Place the cutoff between his own sermons and other voices."""
        if not self.ready:
            self.stats = {"enrolled": len(self.enrolled)}
            self.save()
            return
        centroid = self.centroid()
        # his sermons, each scored against a profile built without it
        own = [self._score(self.embeddings(v).sermon, self.centroid(exclude=v))
               for v in self.enrolled if len(self.embeddings(v).sermon)]
        # other men in the same services, plus whole sermons known to be someone else's
        others = [float(x) for v in self.enrolled if len(self.embeddings(v).other)
                  for x in self.embeddings(v).other @ centroid]
        guests = [self._score(self.embeddings(v).sermon, centroid)
                  for v in self.impostors if self.has_embeddings(v) and len(self.embeddings(v).sermon)]

        own_low = float(np.percentile(own, 5))
        # he sometimes leads a prayer or makes announcements, so ignore the top quarter
        # of "other" windows; guest sermons are reliable and count in full
        highs = ([float(np.percentile(others, 75))] if others else []) + guests
        other_high = max(highs) if highs else None
        if other_high is not None and other_high < own_low:
            threshold = (own_low + other_high) / 2
        else:
            threshold = own_low - 0.15
        threshold = float(np.clip(threshold, 0.25, 0.75))
        self.stats = {
            "enrolled": len(self.enrolled),
            "threshold": round(threshold, 3),
            "own_scores": {"min": round(min(own), 3), "p05": round(own_low, 3),
                           "median": round(float(np.median(own)), 3)},
            "other_voices_p75": None if not others else round(float(np.percentile(others, 75)), 3),
            "guest_sermons_max": None if not guests else round(max(guests), 3),
        }
        self.save()


def find_regular_voice(profile: Profile, video_ids: list[str], similarity: float,
                       min_share: float = 0.3) -> list[str]:
    """Find the voice that preaches most of the services: the largest group of
    sermons that all sound like the same man. Returns their video ids, or []
    if no voice clearly dominates."""
    vids = [v for v in video_ids if profile.has_embeddings(v) and len(profile.embeddings(v).sermon)]
    if len(vids) < profile.settings.min_sermons:
        return []
    means = np.array([_unit(profile.embeddings(v).sermon.mean(axis=0)) for v in vids])
    sims = means @ means.T
    neighbours = sims >= similarity
    center = int(neighbours.sum(axis=1).argmax())
    group = [vids[i] for i in np.flatnonzero(neighbours[center])]
    if len(group) < max(profile.settings.min_sermons, min_share * len(vids)):
        return []
    return group
