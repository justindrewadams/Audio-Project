# Handoff: Sermons - Joshua Adams podcast (`sermonpod`)

*Last updated October 16, 2026. To resume in a new Claude Code session, attach the repo
`justindrewadams/Audio-Project` and say: "Read HANDOFF.md and CLAUDE.md, then continue."*

---

## 1. Purpose

An automated podcast of Joshua Adams's sermons at the **Mountain Home church of Christ,
Mountain Home, Arkansas**.

1. Every Sunday night, find that day's AM and PM **livestreamed worship services** on the church's
   YouTube channel. Skip the Sunday-morning Bible class and the Wednesday-night class.
2. Confirm **Joshua Adams preached**. He is the regular preacher; guest speakers are excluded.
3. Cut out **only the sermon**: no singing, prayers, Lord's Supper, announcements or invitation song.
4. **Enhance** the audio toward studio quality: denoise, EQ, compression, and loudness to −16 LUFS.
5. Publish it as a **podcast** (RSS feed for Apple Podcasts and Spotify), with a phone-friendly status page.

The owner (GitHub `justindrewadams`) runs everything **from an iPhone**. There is no always-on
computer, so the app runs on **GitHub Actions**. Every action the owner takes must be doable in
mobile Safari or the GitHub app.

---

## 2. Current status

| Item | State |
|---|---|
| Code, tests (38 passing), workflow | ✅ Done, on `main` |
| Repo visibility | ✅ **Public** (podcast apps need public audio URLs) |
| GitHub Pages (Source: GitHub Actions) | ✅ Enabled; a deploy has succeeded |
| `ANTHROPIC_API_KEY` secret | ✅ Added by the owner |
| `OWNER_EMAIL` repository variable | ❌ Not set (needed before submitting to Apple Podcasts or Spotify) |
| Cover art | ✅ Final: Joshua cut out on a deep warm charcoal background `(29, 25, 23)` |
| **YouTube access from GitHub** | ❌ **BLOCKED.** This is the only thing preventing episodes. |
| Episodes published | None yet |

### The blocker

From GitHub's runners, YouTube answers every video request with **"Sign in to confirm you're
not a bot."** Listing the channel works: the 8 newest livestream IDs come back, e.g.
`rEZYJUwyTxA`, `9StXMH2pQNo`, `wqF3-Uwl6rs`. Opening any video fails. The `probe` workflow
command tried all 10 yt-dlp player clients (`default, tv, tv_simply, tv_embedded, web_safari,
web_embedded, mweb, ios, android, android_vr`), and **every one failed**. The cause is the IP
address, not the client type.

**Fix options presented to the owner (awaiting their choice):**

1. **Residential proxy (doable entirely on the phone; owner was leaning this way).** Sign up
   with a residential-proxy provider (e.g. Webshare, IPRoyal, Decodo). Choose a US location, a
   sticky session if offered, and pay-as-you-go; about 0.5–1 GB/month is enough. Save
   `http://USER:PASS@HOST:PORT` as the repo secret **`YT_PROXY`**. The code already supports it.
2. **YouTube cookies:** about 10 minutes on any computer, once. Export cookies.txt with a "Get
   cookies.txt" extension, using a throwaway Google account. Save the contents as the secret
   **`YT_COOKIES`**. Cookies expire eventually.
3. **Church shares recordings directly**, e.g. a Drive or Dropbox folder. This would need a
   new input module; nothing for it is built yet.

**Next steps once a secret is added:** run the workflow with `probe` (it should print `OK` for
at least one client), then `test` (processes the newest Sunday service end to end). Listen to the
episode, then switch to the normal weekly `run`.

The owner was also told not to paste secrets (API key, proxy password) into chat. They go
only into GitHub secrets.

---

## 3. Locations and URLs

| What | Where |
|---|---|
| Repository | https://github.com/justindrewadams/Audio-Project (branch `main`) |
| State branch (machine-written) | `sermonpod-data` (orphan branch; worktree mounted at `data/` in CI) |
| Workflow | `.github/workflows/sermonpod.yml`, Actions page: https://github.com/justindrewadams/Audio-Project/actions/workflows/sermonpod.yml |
| Status page | https://justindrewadams.github.io/Audio-Project/ |
| Podcast feed | https://justindrewadams.github.io/Audio-Project/feed.xml |
| Episode MP3s | GitHub release tag **`episodes`**: `https://github.com/justindrewadams/Audio-Project/releases/download/episodes/<file>.mp3` (release is created on the first episode) |
| Add a secret | https://github.com/justindrewadams/Audio-Project/settings/secrets/actions/new |
| YouTube channel | https://www.youtube.com/@mountainhomechurchofchrist3397 (the `/streams` tab is scanned) |
| Church website | mountainhome.church (the photo came from its About page; the site is unreachable from the Claude sandbox) |
| Claude API key | Created by the owner at console.anthropic.com as a separate key from their other project |

---

## 4. Solution architecture

```
                GitHub Actions (weekly cron Mon 06:17 UTC = Sun night Central, or manual "Run workflow")
                ─────────────────────────────────────────────────────────────────────────────
 sermonpod-data ──restore──► data/ (state.json, voice/*.npz, profile.json)
 branch                         │
                                ▼
 YouTube /streams ──► youtube.discover ──► classify (Sunday? class? too short? named?)
   (via YT_PROXY /                │
    YT_COOKIES)                   ▼  status: matched | unnamed | rejected
                     youtube.download_audio (yt-dlp → 48 kHz WAV)
                                  ▼
                     transcribe (faster-whisper small.en, timestamps)
                                  ▼
                     segment.find_sermon ── Claude (claude-opus-5-5, JSON schema):
                                  │          recording type, sermon first/last line,
                                  │          preacher yes/no/unknown, title, scripture, summary
                                  │          (falls back to speaking-rate heuristic on API error)
                                  ▼
                     voice (sherpa-onnx wespeaker embeddings → score vs. his profile)
                                  ▼
                     pipeline.decide ──► accept | reject | review (human decides)
                                  ▼ accept
                     enhance: cut → denoise → EQ/de-ess/compress → 2-pass loudnorm → mono MP3
                                  ▼
                     feed.xml (RSS) + index.html (status page) + cover.jpg
                ─────────────────────────────────────────────────────────────────────────────
   MP3s ──► GitHub release "episodes"      site ──► GitHub Pages      data/ ──► commit to sermonpod-data
```

### Tech stack

Python 3.11+ (CI uses 3.12) · yt-dlp · ffmpeg · faster-whisper · Anthropic Python SDK
(`anthropic>=1.0`) · sherpa-onnx (speaker embeddings) · numpy · Pillow · pytest. CI runs on
`ubuntu-latest`.

---

## 5. Process flow in detail

1. **Restore state.** CI checks out `sermonpod-data` into `data/`, or creates the orphan branch on
   the first run.
2. **Discover** (`youtube.discover`): lists the newest N videos on the `streams` tab (`--scan`)
   and fetches details for IDs not already in state. `classify()` rejects a video if any of these
   is true:
   - it's before `since` (`20260701`),
   - its local weekday isn't **Sunday** (judged in `America/Chicago` from `release_timestamp`),
   - its title matches `\bclass\b` or `bible study`,
   - it's shorter than 15 minutes.

   Otherwise it's `matched` (title or description names him) or `unnamed`. If YouTube refuses
   every lookup, discover **raises**, so the run is reported as failed.
3. **Process** each `matched` or `unnamed` video, newest first, up to `--limit`:
   - download → transcribe → Claude finds the sermon. A Bible class or a recording with no
     sermon → `rejected`.
   - The voice check scores the sermon windows against his profile.
   - `decide()` combines the signals (see §6). A sermon shorter than 8 minutes → `failed`.
   - Enhance → MP3 named `YYYYMMDD-<title-slug>.mp3` with ID3 tags.
   - Confirmed sermons are auto-enrolled into the voice profile.
4. **After processing:** if the profile isn't ready, `bootstrap_profile` finds the largest
   same-voice cluster (he's the regular preacher). `recheck` then re-decides earlier videos.
   A published episode that turns out not to be him is moved to `review`; it's never silently
   deleted.
5. **Publish:** upload new MP3s to the `episodes` release, write feed and status page, build the
   cover from `cloud/photo.png`, commit `data/`, and deploy Pages.

### Manual workflow commands (GitHub app → Actions → Sermon podcast → Run workflow)

| `command` | Does | Needs `video_ids` |
|---|---|---|
| `run` (default, also the cron) | scan newest 30, process up to 8 | no |
| `test` | scan newest 8, process 1 (newest Sunday service) | no |
| `check` | discover only (scan 30), publishes nothing | no |
| `probe` | `cloud/probe.py`: which yt-dlp clients YouTube allows | no |
| `backfill` | full scan, process up to 20 | no |
| `voice yes` | these ARE his sermons → enroll, restore to feed or queue | yes |
| `voice no` | these are NOT his → remove from feed, mark as other speaker | yes |
| `add` | force-include a video | yes |
| `retry` | re-queue a failed or rejected video | yes |

Video IDs are validated as 11-character YouTube IDs, separated by spaces or commas.
`ANTHROPIC_API_KEY` is required for every command except `check`, `probe` and `voice no`.

---

## 6. Who-preached decision logic (`pipeline.decide`)

Signals: **named** (title or description matches `name_patterns`), **claude** (`yes / no / unknown`
from the service itself), **voice** (`yes / no / unknown` from the profile). A video added with
`add` (**forced**) is always accepted.

| voice | other signals | verdict |
|---|---|---|
| yes | claude = no | **review** (sounds like him, but someone else was introduced) |
| yes | otherwise | accept |
| no | named or claude = yes | **review** |
| no | otherwise | reject |
| unknown | named | accept |
| unknown | claude yes / no | accept / reject |
| unknown | claude unknown | `unknown_preacher` setting: **`include`** (current, because he's the regular preacher) or `exclude` |

The truth table is tested in `tests/test_voice.py::test_decide`.

### Voice profile (`sermonpod/voice.py`)

- **Embeddings:** the wespeaker ResNet34 model (26 MB, downloaded from k2-fsa sherpa-onnx GitHub
  releases into `data/models/`) produces 24 six-second windows from inside the sermon (skipping
  its first minute), plus up to 12 "other speaker" windows from spoken parts outside the sermon.
  These are saved per video at `data/voice/<id>.npz`.
- **Profile:** `data/voice/profile.json` holds enrolled IDs (his 60 most recent), impostor IDs
  and calibration stats. It is ready at 3 enrolled sermons.
- **Score:** the median cosine similarity of a sermon's windows to his centroid.
- **Cutoff:** auto-calibrated between his leave-one-out scores (5th percentile) and the other
  voices (75th percentile and guest sermons), clamped to 0.25–0.75. Scores within a 0.08 margin
  below the cutoff count as `unknown`.
- **Verification so far:** checked with the real model on real speech from two speakers (with
  added echo and noise): his unseen clips scored 0.83 → yes; the other speaker scored 0.46 → no.
  It has **not** yet been tested on the church's actual recordings.

---

## 7. File reference

| Path | Role |
|---|---|
| `sermonpod/cli.py` | `sermonpod` CLI: `discover`, `run`, `feed`, `status`, `add`, `retry`, `cover`, `voice {status,yes,no,bootstrap,recheck}` |
| `sermonpod/pipeline.py` | Orchestration: `process_video`, `decide`, `run`, `bootstrap_profile`, `recheck`, `confirm`, `write_feed` |
| `sermonpod/youtube.py` | yt-dlp listing (`list_channel`, newest N), `service_time` (timezone), `classify`, `discover`, `download_audio`; proxy and cookies applied in `_ydl_opts` |
| `sermonpod/transcribe.py` | faster-whisper with VAD, cached to `data/transcripts/<id>.json` |
| `sermonpod/segment.py` | Claude prompt and JSON schema (`claude_span`), `snap_to_segments`, `heuristic_span` fallback, `SermonSpan` |
| `sermonpod/voice.py` | `Embedder`, window selection, `embed_service`, `Profile` (score, decide, enroll, calibrate), `find_regular_voice` |
| `sermonpod/enhance.py` | ffmpeg chain: cut, denoise (`ffmpeg` afftdn / `deepfilternet` / `none`), EQ, de-ess, compressor, two-pass loudnorm, MP3 encode |
| `sermonpod/feed.py` | RSS 2.0 + iTunes tags; `enclosure_url` (uses `audio_base_url`), `service_label` ("Sunday AM, October 12, 2026") |
| `sermonpod/site.py` | Status page: Apple Podcasts subscribe link, "Needs your decision" list, episode players, recently skipped |
| `sermonpod/cover.py` | 3000×3000 cover. A transparent PNG gives the cutout-on-solid layout. Fonts in `cloud/fonts/` |
| `sermonpod/state.py` | JSON state store (atomic writes) |
| `sermonpod/config.py` | TOML → dataclasses `Source, Trim, Enhance, Voice, Podcast, Paths`; rejects unknown keys |
| `cloud/config.toml` | **Production config used by CI**: channel, tabs `["streams"]`, `days=["Sunday"]`, timezone, exclude patterns, `since="20260701"`, Pages/release URLs, podcast title and description |
| `cloud/photo.png` | Joshua's photo with the brick background removed (BiRefNet-portrait via rembg, done in-session) |
| `cloud/cover.jpg` | Rendered cover, also the fallback |
| `cloud/fonts/` | Playfair Display and Montserrat (OFL, licenses included) |
| `cloud/probe.py` | Diagnostic: tests yt-dlp client types (and proxy) against YouTube |
| `config.example.toml` | Template for running on a computer (`/config.toml` is gitignored) |
| `.github/workflows/sermonpod.yml` | CI: restore state → install → secrets → run command → upload MP3s → save state → build site and cover → deploy Pages |
| `tests/test_pipeline.py` | Matching, discovery, Sunday/class filtering, Claude parsing (fake client), audio chain, feed, status page, cover, proxy, blocked-YouTube error, end to end |
| `tests/test_voice.py` | Decision table, profile calibration and bootstrapping with synthetic voiceprints, pipeline voice flow |
| `README.md` | User-facing docs. **Partly out of date:** it doesn't mention `YT_PROXY` or the `test`, `check` and `probe` commands, and its "Run it at home" note predates the cloud setup |
| `CLAUDE.md` | Developer guide for Claude Code sessions (commands, architecture) |

### Podcast metadata (in `cloud/config.toml`)

- **Title:** `Sermons - Joshua Adams`
- **Description:** "Sermons by Joshua Adams from Mountain Home church of Christ located in
  Mountain Home Arkansas. For more information, visit mountainhome.church"
- **Category:** Religion & Spirituality › Christianity · language en-us · not explicit
- **Cover:** "SERMONS" (gold) / **Joshua Adams** (cream, Playfair Display) / gold rule /
  "MOUNTAIN HOME CHURCH OF CHRIST" (cream, Montserrat), on a background of RGB(29, 25, 23)

### Secrets and variables (names only, values are never stored in the repo)

| Name | Type | Status | Used for |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | secret | set | Claude sermon analysis |
| `YT_PROXY` | secret | **not set** | yt-dlp proxy → env `SERMONPOD_PROXY` |
| `YT_COOKIES` | secret | not set | written to `cookies.txt` |
| `OWNER_EMAIL` | variable | not set | `<itunes:email>` in the feed (Apple/Spotify ownership checks; publicly visible) |

---

## 8. Owner preferences and decisions so far

- Only **Sunday AM and PM** services become episodes. Classes (Sunday morning, Wednesday night) are excluded.
- Joshua is the **regular preacher**, so services with an unknown preacher are included by default.
- Runs once a week on **Sunday night**, in the cloud. A closed laptop can't run jobs.
- iPhone only: avoid steps that need a computer when possible.
- Cover iterations went: brick photo → background removed → navy → deep charcoal → **deep charcoal
  with a slight brown tint (final)**.
- A transcript feature was offered and declined ("Forget that").

## 9. Known limitations and open ideas

- **Claude API cost per service is unmeasured.** The owner should check the console's usage page
  after the first runs.
- The photo is small (516×620), so the cover is slightly soft when viewed full size. A
  higher-resolution original would fix that.
- **Audio enhancement:** sanctuary echo can't be fully removed. `denoiser = "deepfilternet"` is
  stronger, but needs PyTorch and isn't enabled in CI.
- **CI runtime:** on a 4-core runner, transcribing about 90 minutes of audio is estimated at
  15–30 minutes per service. The workflow timeout is 330 minutes. This hasn't been measured yet.
- **Hosting:** release assets have no practical size limit for this use. Pages hosts only small files.
- **Not yet verified on real data:** the whole pipeline has only run on synthetic or stubbed inputs.
  The first real `test` run after the YouTube fix is the true end-to-end check: listen to the
  output, check the sermon start and end points, and check the title and summary.
- **Sandbox constraints (Claude Code cloud):** youtube.com, huggingface.co, mountainhome.church
  and github.io are unreachable. GitHub (API, raw, releases) and PyPI work. Workflows can be
  triggered and logs read with the GitHub MCP tools (`actions_run_trigger`, `get_job_logs`).
