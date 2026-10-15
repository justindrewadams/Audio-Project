"""Cut the sermon out of the service audio and master it for podcast playback.

Chain (in order):
  1. cut       - extract [start, end] as 48 kHz mono
  2. denoise   - DeepFilterNet (neural) or ffmpeg's FFT denoiser
  3. tone      - rumble high-pass, de-mud, presence lift, de-ess
  4. dynamics  - gentle compression so quiet and loud passages sit together
  5. loudness  - two-pass EBU R128 normalization to -16 LUFS / -1.5 dBTP
  6. encode    - mono MP3 with ID3 tags
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Enhance

log = logging.getLogger(__name__)

FFMPEG_DENOISE = "afftdn=nr=12:nf=-40:tn=1"

TONE_AND_DYNAMICS = ",".join([
    "highpass=f=75:poles=2",                       # HVAC rumble, handling noise
    "equalizer=f=250:t=q:w=1.2:g=-2.5",            # room boom / muddiness
    "equalizer=f=3200:t=q:w=1.0:g=2.5",            # intelligibility
    "equalizer=f=9000:t=h:w=0.7:g=1.5",            # air
    "deesser=i=0.4:m=0.5:f=0.5",
    "acompressor=threshold=-24dB:ratio=3:attack=8:release=250:knee=6:makeup=4",
    "lowpass=f=16000",
])


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{proc.stderr[-3000:]}")
    return proc


def cut(src: Path, dst: Path, start: float, end: float) -> None:
    _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
          "-i", str(src), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)])


def denoise(src: Path, dst: Path, method: str) -> None:
    if method == "deepfilternet":
        if not shutil.which("deepFilter"):
            raise RuntimeError("denoiser = 'deepfilternet' but the deepFilter CLI is not installed "
                               "(pip install 'sermonpod[deepfilter]')")
        with tempfile.TemporaryDirectory() as tmp:
            _run(["deepFilter", "--output-dir", tmp, str(src)])
            produced = next(Path(tmp).glob("*.wav"))
            shutil.move(produced, dst)
    elif method == "ffmpeg":
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", FFMPEG_DENOISE, str(dst)])
    elif method == "none":
        shutil.copy(src, dst)
    else:
        raise ValueError(f"unknown denoiser {method!r}")


def measure_loudness(src: Path, filters: str, settings: Enhance) -> dict:
    """First loudnorm pass: returns the measured stats as a dict."""
    af = f"{filters},loudnorm=I={settings.target_lufs}:TP={settings.true_peak}:LRA=11:print_format=json"
    proc = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af", af, "-f", "null", "-"])
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not match:
        raise RuntimeError("could not parse loudnorm measurement")
    return json.loads(match.group(0))


def master(src: Path, dst: Path, settings: Enhance, tags: dict[str, str] | None = None,
           fade: float = 0.75) -> None:
    """Tone, compress, two-pass loudness normalize and encode to MP3."""
    duration = probe_duration(src)
    shaping = f"{TONE_AND_DYNAMICS},afade=t=in:d={fade},afade=t=out:st={max(duration - fade, 0):.3f}:d={fade}"
    m = measure_loudness(src, shaping, settings)
    loudnorm = (
        f"loudnorm=I={settings.target_lufs}:TP={settings.true_peak}:LRA=11"
        f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
    )
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", f"{shaping},{loudnorm}",
           "-ar", str(settings.sample_rate), "-ac", "1",
           "-c:a", "libmp3lame", "-b:a", settings.bitrate, "-id3v2_version", "3"]
    for k, v in (tags or {}).items():
        cmd += ["-metadata", f"{k}={v}"]
    _run(cmd + [str(dst)])


def probe_duration(path: Path) -> float:
    proc = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)])
    return float(proc.stdout.strip())


def measure_integrated_lufs(path: Path) -> float:
    proc = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-af", "ebur128", "-f", "null", "-"])
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)[-1])


def produce_episode(raw: Path, dst: Path, start: float, end: float, settings: Enhance,
                    tags: dict[str, str] | None = None) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cut_wav = Path(tmp) / "cut.wav"
        clean_wav = Path(tmp) / "clean.wav"
        cut(raw, cut_wav, start, end)
        denoise(cut_wav, clean_wav, settings.denoiser)
        master(clean_wav, dst, settings, tags)
    return dst
