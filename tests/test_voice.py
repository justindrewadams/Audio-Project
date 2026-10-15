"""Voice-profile logic, using synthetic voiceprints so no model is needed.

Every recording in a church shares the room's sound, so each fake window is
   room + speaker + per-window noise
which makes different men score well above zero against each other, as they do
in real recordings from the same microphone."""

import numpy as np
import pytest

from sermonpod import enhance, pipeline, segment, transcribe, voice, youtube
from sermonpod.config import Config, Enhance, Paths, Podcast, Source, Trim, Voice
from sermonpod.state import State

DIM = 64
rng = np.random.default_rng(42)
ROOM = rng.standard_normal(DIM)
ROOM /= np.linalg.norm(ROOM)


def speaker():
    v = rng.standard_normal(DIM)
    return v / np.linalg.norm(v)


HIM = speaker()


def windows(spk, n):
    noise = rng.standard_normal((n, DIM))
    noise /= np.linalg.norm(noise, axis=1, keepdims=True)
    w = 0.5 * ROOM + spk + 0.6 * noise
    return (w / np.linalg.norm(w, axis=1, keepdims=True)).astype(np.float32)


def service(preacher, n=24):
    others = np.vstack([windows(speaker(), 3) for _ in range(4)])  # prayer, reading, ...
    return voice.ServiceEmbeddings(sermon=windows(preacher, n), other=others)


# ---------------------------------------------------------------- decision table

@pytest.mark.parametrize("named,claude,vm,policy,expected", [
    (False, "unknown", "yes", "exclude", "accept"),
    (False, "no", "yes", "include", "review"),     # introduced a guest, but sounds like him
    (False, "unknown", "no", "include", "reject"),
    (True, "unknown", "no", "include", "review"),  # title names him, voice disagrees
    (False, "yes", "no", "include", "review"),
    (False, "unknown", "unknown", "include", "accept"),
    (False, "unknown", "unknown", "exclude", "reject"),
    (False, "no", "unknown", "include", "reject"),
    (True, "unknown", "unknown", "exclude", "accept"),
])
def test_decide(named, claude, vm, policy, expected):
    verdict, _ = pipeline.decide(named=named, forced=False, claude=claude, voice_match=vm,
                                 unknown_policy=policy)
    assert verdict == expected


def test_forced_always_accepts():
    assert pipeline.decide(named=False, forced=True, claude="no", voice_match="no",
                           unknown_policy="exclude")[0] == "accept"


# ---------------------------------------------------------------- profile

def make_profile(tmp_path, **kw):
    return voice.Profile(tmp_path / "voice", Voice(**kw))


def test_profile_needs_min_sermons(tmp_path):
    prof = make_profile(tmp_path, min_sermons=3)
    for i in range(2):
        prof.store(f"s{i}", service(HIM))
        prof.enroll(f"s{i}")
    assert not prof.ready
    assert prof.decide(prof.score(service(HIM))) == "unknown"


def test_profile_recognizes_him_and_rejects_guests(tmp_path):
    prof = make_profile(tmp_path)
    for i in range(6):
        prof.store(f"s{i}", service(HIM))
        prof.enroll(f"s{i}")
    assert prof.ready
    stats = prof.stats
    assert stats["other_voices_p75"] < prof.threshold < stats["own_scores"]["p05"]

    him_scores = [prof.score(service(HIM)) for _ in range(20)]
    guest_scores = [prof.score(service(speaker())) for _ in range(20)]
    assert all(prof.decide(s) == "yes" for s in him_scores)
    assert all(prof.decide(s) == "no" for s in guest_scores)

    # survives reload from disk
    again = make_profile(tmp_path)
    assert again.enrolled == prof.enrolled and again.threshold == prof.threshold


def test_profile_keeps_most_recent(tmp_path):
    prof = make_profile(tmp_path, max_sermons=4)
    for i in range(6):
        prof.store(f"s{i}", service(HIM))
        prof.enroll(f"s{i}")
    assert prof.enrolled == ["s2", "s3", "s4", "s5"]


def test_find_regular_voice(tmp_path):
    prof = make_profile(tmp_path)
    ids = []
    for i in range(5):
        prof.store(f"him{i}", service(HIM)); ids.append(f"him{i}")
    for i in range(2):
        prof.store(f"guest{i}", service(speaker())); ids.append(f"guest{i}")
    assert sorted(voice.find_regular_voice(prof, ids, similarity=0.7)) == [f"him{i}" for i in range(5)]


def test_no_regular_voice_when_nobody_dominates(tmp_path):
    prof = make_profile(tmp_path)
    ids = [f"g{i}" for i in range(6)]
    for vid in ids:
        prof.store(vid, service(speaker()))
    assert voice.find_regular_voice(prof, ids, similarity=0.7) == []


# ---------------------------------------------------------------- pipeline

@pytest.fixture
def fake_world(tmp_path, monkeypatch):
    """Services where nobody is introduced; video ids starting with 'g' are guest preachers."""
    guests = {}

    def fake_embed(embedder, audio, segments, start, end, settings):
        vid = audio.stem
        return service(guests.setdefault(vid, speaker()) if vid.startswith("g") else HIM)

    def fake_produce(raw, dst, *a, **k):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"mp3")
        return dst

    def fake_download(src, vid, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{vid}.wav").write_bytes(b"")
        return dest / f"{vid}.wav"

    monkeypatch.setattr(youtube, "download_audio", fake_download)
    monkeypatch.setattr(transcribe, "transcribe", lambda *a: [])
    monkeypatch.setattr(segment, "find_sermon", lambda *a: segment.SermonSpan(start=600, end=2400))
    monkeypatch.setattr(voice, "embed_service", fake_embed)
    monkeypatch.setattr(voice, "Embedder", lambda *a: None)
    monkeypatch.setattr(enhance, "produce_episode", fake_produce)
    monkeypatch.setattr(enhance, "probe_duration", lambda p: 1800.0)
    monkeypatch.setattr(pipeline, "_embedder", None)

    cfg = Config(
        source=Source(channel_url="x", unknown_preacher="include"),
        trim=Trim(), enhance=Enhance(),
        podcast=Podcast(title="P", author="A", description="D", base_url="https://ex.com"),
        paths=Paths(work_dir=tmp_path / "data", output_dir=tmp_path / "out"),
        voice=Voice(),
    )
    def queue(*vids):
        state = State(cfg.paths.state_file)
        for i, vid in enumerate(vids):
            state.upsert(vid, status="unnamed", named=False, title=f"Sunday Worship {vid}",
                         upload_date=f"202601{i + 10:02d}", url=f"https://youtu.be/{vid}")

    return cfg, queue


def test_profile_bootstraps_and_catches_guests(fake_world):
    cfg, queue = fake_world
    queue("a1", "a2", "a3", "g1", "a4", "a5")
    pipeline.run(cfg, discover=False)
    state = State(cfg.paths.state_file)

    # nobody was introduced, so everything was included at first; then the
    # profile started itself from the recurring voice and flagged the guest
    prof = voice.Profile(cfg.paths.voice_dir, cfg.voice)
    assert sorted(prof.enrolled) == ["a1", "a2", "a3", "a4", "a5"]
    assert state.get("g1")["status"] == "review"
    assert all(state.get(v)["status"] == "done" for v in ["a1", "a2", "a3", "a4", "a5"])
    assert '<guid isPermaLink="false">youtube:g1</guid>' not in (cfg.paths.output_dir / "feed.xml").read_text()

    # new services are now judged by voice as they come in
    queue("a6", "g2")
    pipeline.run(cfg, discover=False)
    state = State(cfg.paths.state_file)
    assert state.get("a6")["status"] == "done" and state.get("a6")["voice_match"] == "yes"
    assert state.get("g2")["status"] == "rejected" and state.get("g2")["voice_match"] == "no"
    assert "a6" in voice.Profile(cfg.paths.voice_dir, cfg.voice).enrolled

    # a person settles the flagged one; it becomes a known other speaker
    pipeline.confirm(cfg, state, ["g1"], is_him=False)
    assert state.get("g1")["status"] == "rejected"
    assert "g1" in voice.Profile(cfg.paths.voice_dir, cfg.voice).impostors
