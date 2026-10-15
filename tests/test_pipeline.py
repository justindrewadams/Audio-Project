import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from sermonpod import enhance, feed, segment, youtube
from sermonpod.config import Enhance, Podcast, Source, Voice
from sermonpod.state import State


# ---------------------------------------------------------------- helpers

def fake_transcript():
    """10 min singing (~60 wpm), 30 min preaching (~150 wpm), 5 min singing."""
    segs, t = [], 0.0
    for minutes, wpm in [(10, 60), (30, 150), (5, 60)]:
        end = t + minutes * 60
        while t < end:
            n = int(wpm * 10 / 60)
            segs.append({"start": t, "end": t + 10, "text": " ".join(["word"] * n)})
            t += 10
    return segs


def write_noisy_speechlike(path: Path, seconds=20, sr=48000):
    """Syllable-rate modulated harmonics under steady hiss, quiet like a room mic."""
    rng = np.random.default_rng(0)
    t = np.arange(int(seconds * sr)) / sr
    voice = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate([140, 280, 420, 560, 1200, 2400], 1))
    envelope = (np.sin(2 * np.pi * 4 * t) > 0).astype(float) * (np.sin(2 * np.pi * 0.3 * t) > -0.5)
    signal = 0.05 * voice * envelope + 0.01 * rng.standard_normal(t.size)
    pcm = (np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(sr), "-ac", "1",
                    "-i", "-", str(path)], input=pcm, check=True)


# ---------------------------------------------------------------- matching

def test_name_matching():
    m = youtube.compile_matcher(["Joshua Adams", "Josh Adams", r"Bro\.? Adams"])
    assert youtube.names_preacher({"title": "Faith That Works - Josh Adams"}, m)
    assert youtube.names_preacher({"title": "Sunday AM", "description": "Speaker: bro. adams"}, m)
    assert not youtube.names_preacher({"title": "Sunday AM", "description": "Speaker: John Smith"}, m)


def test_discover_classifies(tmp_path, monkeypatch):
    infos = {
        "a": {"title": "Grace - Joshua Adams", "duration": 2400, "upload_date": "20260104"},
        "b": {"title": "Sunday PM Worship", "duration": 3600, "upload_date": "20260104"},
        "c": {"title": "Announcements", "duration": 120, "upload_date": "20260104"},
        "d": {"title": "Joshua Adams", "duration": 3000, "upload_date": "20200101"},
    }
    monkeypatch.setattr(youtube, "list_channel", lambda src, newest=None: [{"id": k} for k in infos])
    monkeypatch.setattr(youtube, "video_details", lambda src, vid: infos[vid])
    state = State(tmp_path / "s.json")
    youtube.discover(Source(channel_url="x", name_patterns=["Joshua Adams"], since="20250101"), state)
    assert {k: state.get(k)["status"] for k in infos} == {
        "a": "matched", "b": "unnamed", "c": "rejected", "d": "rejected"}
    # second run doesn't re-fetch known videos
    monkeypatch.setattr(youtube, "video_details", lambda *a: pytest.fail("refetched"))
    assert youtube.discover(Source(channel_url="x"), state) == []


# ---------------------------------------------------------------- segmentation

def test_heuristic_finds_preaching():
    span = segment.heuristic_span(fake_transcript())
    assert abs(span.start - 600) <= 30
    assert abs(span.end - 2400) <= 30


class FakeClient:
    def __init__(self, payload, stop_reason="end_turn"):
        msg = SimpleNamespace(stop_reason=stop_reason,
                              content=[SimpleNamespace(type="thinking"),
                                       SimpleNamespace(type="text", text=json.dumps(payload))])
        stream = SimpleNamespace(get_final_message=lambda: msg,
                                 __enter__=None, __exit__=None)

        class Ctx:
            def __enter__(self_inner):
                return stream

            def __exit__(self_inner, *a):
                return False

        self.calls = []

        def stream_fn(**kw):
            self.calls.append(kw)
            return Ctx()

        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream_fn))


def test_claude_span_snaps_to_segments():
    segs = fake_transcript()
    client = FakeClient({
        "sermon_found": True, "first_line_timestamp": 601.0, "last_line_timestamp": 2390.0,
        "preacher": "Joshua Adams", "preacher_match": "yes", "title": "Living Faith",
        "scripture": ["James 2:14-26"], "summary": "A lesson on faith.",
    })
    span = segment.claude_span(segs, {"id": "x", "title": "t"}, "Joshua Adams", "claude-opus-5-5", client)
    assert (span.start, span.end) == (600.0, 2400.0)
    assert span.title == "Living Faith" and span.preacher_match == "yes"
    req = client.calls[0]
    assert req["model"] == "claude-opus-5-5"
    assert "[600.0] word" in req["messages"][0]["content"]


def test_claude_refusal_returns_none():
    client = FakeClient({}, stop_reason="refusal")
    assert segment.claude_span(fake_transcript(), {"id": "x"}, "J", "m", client) is None


# ---------------------------------------------------------------- audio

def test_produce_episode_trims_and_normalizes(tmp_path):
    raw = tmp_path / "raw.wav"
    write_noisy_speechlike(raw, seconds=30)
    out = tmp_path / "ep.mp3"
    settings = Enhance(denoiser="ffmpeg")
    enhance.produce_episode(raw, out, start=5, end=25, settings=settings, tags={"title": "T"})
    assert abs(enhance.probe_duration(out) - 20) < 0.5
    assert abs(enhance.measure_integrated_lufs(out) - settings.target_lufs) < 1.0
    tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title",
                           "-of", "default=nw=1:nk=1", str(out)], capture_output=True, text=True).stdout
    assert tags.strip() == "T"


# ---------------------------------------------------------------- feed

def test_feed(tmp_path):
    pod = Podcast(title="Sermons", author="Joshua Adams", description="d",
                  base_url="https://ex.com/pod/", owner_email="a@b.c", image="cover.jpg")
    eps = [
        {"id": "a", "title": "Old", "upload_date": "20260104", "file": "episodes/a.mp3",
         "bytes": 100, "episode_duration": 1925, "url": "https://youtu.be/a"},
        {"id": "b", "title": "Raw title", "episode_title": "Living Faith", "upload_date": "20260111",
         "file": "episodes/b.mp3", "bytes": 200, "episode_duration": 61, "url": "https://youtu.be/b",
         "summary": "Faith & works", "scripture": ["James 2"]},
    ]
    root = ET.parse(feed.build_feed(pod, eps, tmp_path)).getroot()
    items = root.findall("channel/item")
    assert [i.findtext("title") for i in items] == ["Living Faith", "Old"]
    enc = items[0].find("enclosure").attrib
    assert enc == {"url": "https://ex.com/pod/episodes/b.mp3", "length": "200", "type": "audio/mpeg"}
    assert items[0].findtext(f"{{{feed.ITUNES}}}duration") == "0:01:01"
    assert "Faith &amp; works" in items[0].findtext("description")
    cat = root.find(f"channel/{{{feed.ITUNES}}}category")
    assert cat.get("text") == "Religion & Spirituality"
    assert cat.find(f"{{{feed.ITUNES}}}category").get("text") == "Christianity"


# ---------------------------------------------------------------- end to end

def test_pipeline_end_to_end(tmp_path, monkeypatch):
    from sermonpod import pipeline, transcribe
    from sermonpod.config import Config, Paths, Trim

    service = tmp_path / "service.wav"
    write_noisy_speechlike(service, seconds=45 * 60, sr=16000)

    def fake_download(src, vid, dest):
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{vid}.wav"
        out.write_bytes(service.read_bytes())
        return out

    monkeypatch.setattr(youtube, "list_channel", lambda src, newest=None: [{"id": "vid1"}])
    monkeypatch.setattr(youtube, "video_details", lambda src, vid: {
        "title": "Living Faith | Joshua Adams", "duration": 2700, "upload_date": "20261011",
        "webpage_url": "https://www.youtube.com/watch?v=vid1"})
    monkeypatch.setattr(youtube, "download_audio", fake_download)
    monkeypatch.setattr(transcribe, "transcribe", lambda *a: fake_transcript())

    cfg = Config(
        source=Source(channel_url="x", name_patterns=["Joshua Adams"]),
        trim=Trim(method="heuristic"),
        enhance=Enhance(),
        podcast=Podcast(title="P", author="A", description="D", base_url="https://ex.com"),
        paths=Paths(work_dir=tmp_path / "data", output_dir=tmp_path / "out"),
        voice=Voice(enabled=False),
    )
    pipeline.run(cfg)

    ep = State(cfg.paths.state_file).get("vid1")
    assert ep["status"] == "done", ep
    assert abs(ep["episode_duration"] - 1800 - 2 * cfg.trim.pad_seconds) < 2
    assert (cfg.paths.output_dir / ep["file"]).exists()
    assert not (cfg.paths.audio_dir / "vid1.wav").exists()  # raw audio cleaned up
    root = ET.parse(cfg.paths.output_dir / "feed.xml").getroot()
    assert root.find("channel/item/enclosure").get("url") == f"https://ex.com/{ep['file']}"


@pytest.mark.parametrize("match,policy,expected", [
    ("yes", "exclude", "done"),
    ("no", "include", "rejected"),
    ("unknown", "include", "done"),
    ("unknown", "exclude", "rejected"),
])
def test_unnamed_livestream_preacher_policy(tmp_path, monkeypatch, match, policy, expected):
    from sermonpod import enhance as enh, pipeline, transcribe
    from sermonpod.config import Config, Paths, Trim

    monkeypatch.setattr(youtube, "download_audio", lambda *a: tmp_path / "raw.wav")
    monkeypatch.setattr(transcribe, "transcribe", lambda *a: [])
    monkeypatch.setattr(segment, "find_sermon", lambda *a: segment.SermonSpan(
        start=600, end=2400, preacher="Someone" if match == "no" else "", preacher_match=match))

    def fake_produce(raw, dst, *a, **k):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"mp3")
        return dst
    monkeypatch.setattr(enh, "produce_episode", fake_produce)
    monkeypatch.setattr(enh, "probe_duration", lambda p: 1800.0)

    cfg = Config(
        source=Source(channel_url="x", unknown_preacher=policy),
        trim=Trim(), enhance=Enhance(),
        podcast=Podcast(title="P", author="A", description="D", base_url="https://ex.com"),
        paths=Paths(work_dir=tmp_path / "data", output_dir=tmp_path / "out"),
        voice=Voice(enabled=False),
    )
    state = State(cfg.paths.state_file)
    video = state.upsert("v", status="unnamed", title="Sunday Morning Worship",
                         upload_date="20261011", url="https://youtu.be/v")
    pipeline.process_video(cfg, state, video)
    assert state.get("v")["status"] == expected


# ---------------------------------------------------------------- sunday services only

def _ts(iso):
    from datetime import datetime
    return int(datetime.fromisoformat(iso).timestamp())


@pytest.mark.parametrize("title,start,expected,reason", [
    # Sunday 6pm Central is already Monday in UTC; it must still count as Sunday PM
    ("Sunday Evening Worship", "2026-10-12T00:05:00+00:00", "unnamed", ""),
    ("Sunday Morning Worship", "2026-10-11T15:30:00+00:00", "unnamed", ""),
    ("Sunday Morning Bible Class", "2026-10-11T14:30:00+00:00", "rejected", "excluded"),
    ("Wednesday Night Class", "2026-10-15T00:00:00+00:00", "rejected", "Wednesday"),
    ("Midweek Devotional", "2026-10-15T00:00:00+00:00", "rejected", "Wednesday"),
])
def test_only_sunday_services(title, start, expected, reason):
    src = Source(channel_url="x")
    info = {"id": "v", "title": title, "duration": 3600, "release_timestamp": _ts(start),
            "upload_date": "20261012"}
    status, why, record = youtube.classify(info, src, youtube.compile_matcher(src.name_patterns))
    assert status == expected and reason in why
    if expected != "rejected":
        assert record["weekday"] == "Sunday" and record["upload_date"] == "20261011"
        assert record["service"] == ("PM" if "Evening" in title else "AM")


def test_claude_reports_bible_class():
    client = FakeClient({
        "recording": "bible_class", "sermon_found": False, "first_line_timestamp": 0,
        "last_line_timestamp": 0, "preacher": "", "preacher_match": "unknown", "title": "",
        "scripture": [], "summary": "",
    })
    span = segment.claude_span(fake_transcript(), {"id": "x"}, "Joshua Adams", "m", client)
    assert span.recording == "bible_class" and span.duration == 0
    assert "Bible classes" in client.calls[0]["system"]


def test_status_page_and_release_audio(tmp_path):
    from sermonpod import site
    pod = Podcast(title="Sermons", author="A", description="d", base_url="https://ex.github.io/repo",
                  audio_base_url="https://github.com/o/r/releases/download/episodes", image="cover.jpg")
    videos = [
        {"id": "a", "status": "done", "title": "t", "episode_title": "Living Faith", "upload_date": "20261011",
         "service": "AM", "file": "episodes/20261011-living-faith.mp3", "bytes": 1, "episode_duration": 1800,
         "url": "https://youtu.be/a", "voice_score": 0.81},
        {"id": "bbbbbbbbbbb", "status": "review", "title": "Sunday PM", "upload_date": "20261011",
         "service": "PM", "url": "https://youtu.be/b", "reason": "voice doesn't match"},
    ]
    assert feed.enclosure_url(pod, videos[0]) == \
        "https://github.com/o/r/releases/download/episodes/20261011-living-faith.mp3"
    page = site.build_site(pod, videos, tmp_path, "https://github.com/o/r/actions").read_text()
    assert "podcasts://ex.github.io/repo/feed.xml" in page
    assert "Sunday AM, October 11, 2026" in page
    assert "Needs your decision" in page and "bbbbbbbbbbb" in page
    assert "releases/download/episodes/20261011-living-faith.mp3" in page


def test_cover_art_from_portrait(tmp_path):
    from PIL import Image
    from sermonpod.cover import make_cover

    photo = tmp_path / "portrait.jpg"
    Image.new("RGB", (1200, 1800), (180, 150, 120)).save(photo)  # tall portrait
    out = make_cover(photo, tmp_path / "cover.jpg")
    img = Image.open(out)
    assert img.size == (3000, 3000) and img.format == "JPEG"
    # bottom band is darkened for the title; top keeps the photo
    assert sum(img.getpixel((1500, 2900))) < 120 < sum(img.getpixel((1500, 300)))


def test_cover_art_from_cutout(tmp_path):
    from PIL import Image, ImageDraw
    from sermonpod.cover import SHADOW, make_cover

    photo = tmp_path / "cutout.png"
    img = Image.new("RGBA", (500, 600), (0, 0, 0, 0))  # transparent background
    ImageDraw.Draw(img).ellipse((150, 40, 350, 300), fill=(200, 160, 130, 255))  # "head"
    img.save(photo)
    out = Image.open(make_cover(photo, tmp_path / "cover.jpg")).convert("RGB")
    near = lambda px, ref: all(abs(a - b) <= 6 for a, b in zip(px, ref))
    assert near(out.getpixel((60, 60)), SHADOW)          # solid background, no photo
    assert not near(out.getpixel((1500, 700)), SHADOW)  # the subject is there
    assert near(out.getpixel((2900, 2300)), SHADOW)     # title area is solid colour


def test_discover_fails_loudly_when_youtube_blocks_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "list_channel", lambda src, newest=None: [{"id": f"v{i}"} for i in range(5)])
    monkeypatch.setattr(youtube, "video_details", lambda src, vid: None)  # "Sign in to confirm..."
    with pytest.raises(RuntimeError, match="blocking"):
        youtube.discover(Source(channel_url="x"), State(tmp_path / "s.json"))


def test_proxy_from_environment(monkeypatch):
    monkeypatch.setenv("SERMONPOD_PROXY", "http://u:p@proxy.example:8080")
    assert youtube._ydl_opts(Source(channel_url="x"))["proxy"] == "http://u:p@proxy.example:8080"
    monkeypatch.delenv("SERMONPOD_PROXY")
    assert "proxy" not in youtube._ydl_opts(Source(channel_url="x"))
