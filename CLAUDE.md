# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`sermonpod` turns Joshua Adams's sermons from the Mountain Home (Arkansas) church of Christ YouTube livestreams into a podcast. It only uses Sunday AM/PM worship services and skips the Sunday-morning and Wednesday-night Bible classes. The owner runs it entirely from an iPhone, so the real deployment is the GitHub Actions workflow. Changes the owner has to make should be doable in mobile Safari or the GitHub app.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'                         # needs ffmpeg on PATH

pytest -q                                       # full suite (~2 min; one end-to-end test renders 45 min of audio)
pytest -q --deselect tests/test_pipeline.py::test_pipeline_end_to_end   # fast (~10 s)
pytest -q tests/test_voice.py::test_decide      # single test
actionlint .github/workflows/sermonpod.yml      # pip install actionlint-py; run after any workflow edit

sermonpod -c cloud/config.toml discover --scan 8   # CLI; see `sermonpod -h`
sermonpod cover cloud/photo.png -o cover.jpg       # regenerate cover art
```

There is no linter or formatter configured. Tests never touch the network: YouTube, Whisper, Claude and the speaker model are all stubbed with `monkeypatch`.

## Architecture

The pipeline lives in `pipeline.process_video` and `pipeline.run`, which tie together single-purpose modules:

`youtube` (list channel tab → classify by metadata → download WAV) → `transcribe` (faster-whisper, cached JSON) → `segment` (find sermon span) → `voice` (speaker embeddings) → `pipeline.decide` (accept / reject / review) → `enhance` (cut, denoise, EQ/compress, two-pass loudnorm → MP3) → `feed` (RSS) + `site` (status page `index.html`).

Key ideas that span files:

- **State machine in `data/state.json`** (`state.State`), keyed by YouTube video ID. Statuses: `matched` (title names him) / `unnamed` → `done` | `rejected` | `review` | `failed`. Only `matched` and `unnamed` get processed. Discovery skips IDs already in state, so runs are incremental. `named`, `forced` (manual `add`), `claude_match` and `voice_match` are stored per video, so `pipeline.recheck` can re-decide past videos without reprocessing them.
- **Who preached** is decided in `pipeline.decide()`, which combines three signals: title/description regex (`named`), Claude's reading of the service (`preacher_match` yes/no/unknown) and the voiceprint (`voice_match`). Conflicts become `review` for a human. They are never guessed. `test_voice.py::test_decide` is the truth table.
- **Sermon boundaries** (`segment.py`): Claude gets the timestamped transcript and returns structured JSON (`output_config` json_schema). Server-side refusal fallback is on (`fallbacks="default"`), and the default model is `claude-opus-5-5`. Claude also returns the recording type, so Bible classes are rejected. A returned `SermonSpan` with zero duration means "no sermon / class", and `None` means the analysis failed. If the API call raises, it falls back to `heuristic_span`, which uses speaking rate because singing transcribes at a much lower WPM than preaching.
- **Voice profile** (`voice.py`): sherpa-onnx wespeaker embeddings. The model is downloaded from GitHub releases on first use. Per-video `.npz` files hold `sermon` windows and `other` windows (other speakers in the same service). `Profile` keeps enrolled and impostor video IDs. It calibrates its own threshold between his leave-one-out scores and the other voices, because everyone is recorded through the same microphone and room. It bootstraps itself (`find_regular_voice`: the largest same-voice cluster, since he is the regular preacher), and it auto-enrolls confirmed sermons (capped at `max_sermons`).
- **Service date/time**: `youtube.service_time` converts `release_timestamp` to `America/Chicago`. YouTube's UTC `upload_date` would put Sunday-evening services on Monday. `upload_date` in state holds this local date.
- **Config**: `config.load_config` maps TOML sections onto dataclasses and rejects unknown keys. `cloud/config.toml` is what CI uses. `config.example.toml` is for local runs (`/config.toml` is gitignored).

## Cloud deployment (`.github/workflows/sermonpod.yml`)

- Runs on a weekly cron (Monday 06:17 UTC, i.e. Sunday night Central) plus `workflow_dispatch`. The `command` input can be `run`, `test` (newest Sunday service only), `check` (discover only), `probe` (`cloud/probe.py`: which yt-dlp clients YouTube lets through), `backfill`, `voice yes|no`, `add` or `retry`, with `video_ids` where needed.
- **Persistence:** `data/` is a git worktree of the orphan branch `sermonpod-data`, committed after each run. `raw/`, `transcripts/` and `models/` are gitignored there.
- **Publishing:** MP3s are uploaded as assets of the GitHub release tagged `episodes` (`podcast.audio_base_url`). `feed.xml`, `index.html` and `cover.jpg` deploy to GitHub Pages. The cover is rebuilt each run from `cloud/photo.*`. A transparent PNG triggers the solid-background cutout layout in `cover.py`.
- **Secrets and variables:** `ANTHROPIC_API_KEY` (required for anything except `check`, `probe` and `voice no`), `YT_PROXY` → env `SERMONPOD_PROXY` (overrides `source.proxy`), `YT_COOKIES` → `cookies.txt`, and the repo variable `OWNER_EMAIL`, which is injected into the config at runtime.

## Current blocker

YouTube answers every request from GitHub's runners with "Sign in to confirm you're not a bot". The probe run tried all ten yt-dlp player clients and every one failed. Nothing can be processed in CI until a `YT_PROXY` (residential proxy) or `YT_COOKIES` secret is added. `youtube.discover` raises if every detail lookup fails, so such a run is reported as failed and not silently as successful. This sandbox's network also blocks youtube.com and huggingface.co, so local end-to-end runs against the real channel aren't possible here; verify via workflow runs, triggered and read through the GitHub MCP tools.
