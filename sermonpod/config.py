"""Load config.toml into typed settings."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Source:
    channel_url: str
    preacher: str = "Joshua Adams"
    name_patterns: list[str] = field(default_factory=lambda: ["Joshua Adams", "Josh Adams"])
    check_unnamed_videos: bool = False
    since: str = ""
    min_minutes: float = 15
    cookies_file: str = ""


@dataclass
class Trim:
    method: str = "claude"
    model: str = "claude-opus-5-5"
    whisper_model: str = "small.en"
    pad_seconds: float = 1.5


@dataclass
class Enhance:
    denoiser: str = "ffmpeg"
    target_lufs: float = -16.0
    true_peak: float = -1.5
    bitrate: str = "96k"
    sample_rate: int = 44100


@dataclass
class Podcast:
    title: str
    author: str
    description: str
    base_url: str
    language: str = "en-us"
    category: str = "Religion & Spirituality"
    subcategory: str = "Christianity"
    explicit: bool = False
    owner_name: str = ""
    owner_email: str = ""
    image: str = ""


@dataclass
class Paths:
    work_dir: Path = Path("data")
    output_dir: Path = Path("output/podcast")

    def __post_init__(self):
        self.work_dir = Path(self.work_dir)
        self.output_dir = Path(self.output_dir)

    @property
    def audio_dir(self) -> Path:
        return self.work_dir / "raw"

    @property
    def transcript_dir(self) -> Path:
        return self.work_dir / "transcripts"

    @property
    def state_file(self) -> Path:
        return self.work_dir / "state.json"

    @property
    def episodes_dir(self) -> Path:
        return self.output_dir / "episodes"


@dataclass
class Config:
    source: Source
    trim: Trim
    enhance: Enhance
    podcast: Podcast
    paths: Paths


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown keys in [{cls.__name__.lower()}]: {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return Config(
        source=_build(Source, raw.get("source", {})),
        trim=_build(Trim, raw.get("trim", {})),
        enhance=_build(Enhance, raw.get("enhance", {})),
        podcast=_build(Podcast, raw.get("podcast", {})),
        paths=_build(Paths, raw.get("paths", {})),
    )
