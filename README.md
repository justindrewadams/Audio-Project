# sermonpod

Turns Joshua Adams's sermons on the Mountain Home church of Christ YouTube channel
into a podcast:

1. **Find**: scans the channel's Live tab, where each full worship service is
   recorded. To decide whether Joshua preached, it combines three clues: his
   name in the title, what the service itself says (introductions, people
   addressing him), and **his voice** (see below).
2. **Download**: pulls the audio with `yt-dlp`.
3. **Trim**: transcribes the service with Whisper. Claude then reads the
   timestamped transcript and marks where the sermon starts and ends, which cuts
   out the singing, prayers, Lord's Supper and announcements. Claude also writes
   the episode title, scripture list and summary.
4. **Enhance**: removes noise, then rolls off rumble, cuts room boom, lifts
   presence, de-esses, compresses gently, and does two-pass loudness
   normalization to −16 LUFS / −1.5 dBTP (the Apple/Spotify spoken-word target).
   The result is a mono MP3 with ID3 tags.
5. **Publish**: writes `output/podcast/feed.xml`, an RSS feed that Apple Podcasts
   and Spotify accept.

Runs are incremental. `data/state.json` records every video it has seen, so
each run only processes new uploads.

## Setup

Requires Python 3.11+ and `ffmpeg`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                    # add '.[deepfilter]' for the neural denoiser
cp config.example.toml config.toml  # then edit base_url and owner_email
export ANTHROPIC_API_KEY=sk-ant-... # used for sermon boundaries and show notes
```

## Usage

```bash
sermonpod discover            # scan the channel and classify videos (no downloads)
sermonpod status              # see what was matched / rejected and why
sermonpod run --limit 1       # process the newest sermon, as a test
sermonpod run                 # process everything new and rebuild feed.xml
```

Fixing mistakes:

```bash
sermonpod add VIDEO_ID                       # include a video the matcher missed
sermonpod add VIDEO_ID --start 1520 --end 3710   # ...with hand-picked sermon times (seconds)
sermonpod retry VIDEO_ID                     # re-queue a failed/rejected video
```

To pick up new sermons each week, schedule `sermonpod run` (cron, Task Scheduler,
launchd), for example on Sunday night and Wednesday night.

## Hosting the podcast

Upload everything in `output/podcast/` (`feed.xml`, `episodes/`, `cover.jpg`) to
any static host. Set `base_url` in `config.toml` to the public URL of that folder.
Then submit `https://…/feed.xml` once to Apple Podcasts Connect and to Spotify
for Creators. After that they pick up new episodes on their own.

A 35-minute sermon comes out to about 25 MB, so a year of sermons can grow past
2 GB. GitHub Pages does not suit that much audio. Cloudflare R2 (no download
fees) or Amazon S3 work well:

```bash
aws s3 sync output/podcast s3://my-bucket/podcast --acl public-read
```

## Voice profile

The app learns what Joshua sounds like and uses that to tell his sermons from
guest speakers', even when nobody says who is preaching.

- **How it works.** A speaker-recognition model turns every few seconds of a
  sermon into a voiceprint. His profile is the average of his sermons. A new
  sermon is compared against it and gets a score from about 0.3 (a different
  man) to 1.0 (an identical voice).
- **It starts itself.** He is the regular preacher, so after the first few
  services the app finds the one voice that preaches most of them and builds
  the profile from those sermons. It then rechecks everything already
  processed. Any episode that turns out to be someone else is held for review
  and is not left in the feed.
- **It keeps learning.** Every sermon confirmed as his is added to the profile,
  which keeps his 60 most recent sermons. That lets it follow changes in
  microphone or room.
- **It sets its own cutoff.** The cutoff between "him" and "not him" is
  calibrated from your recordings. The app compares how closely his own sermons
  match the profile with how closely the other men in the same services do
  (prayers, readings, announcements). This matters because everyone shares the
  same microphone and room.
- **Disagreements go to you.** If the voice and the service disagree (for
  example the title names him but the voice doesn't match), the video is marked
  `review` instead of guessed.

```bash
sermonpod voice status            # profile size, cutoff, and every video's score
sermonpod voice yes VIDEO_ID ...  # these ARE his: adds to profile / restores to feed
sermonpod voice no VIDEO_ID ...   # these are NOT his: removes from feed, teaches the profile
sermonpod voice bootstrap         # start the profile now instead of waiting
sermonpod voice recheck           # re-score everything with the current profile
```

The speaker model (26 MB) downloads on first use. Set `[voice] enabled = false`
to turn this off.

## Notes and limits

- **Run it at home, not in the cloud.** YouTube often blocks downloads from
  data-center IP addresses. If it asks you to sign in, export your browser
  cookies to a `cookies.txt` file and set `cookies_file`.
- **"Studio quality."** The chain removes hiss, hum, rumble and level swings, and
  makes the speech clear and consistent. It cannot fully remove the sanctuary's
  echo, because that is recorded into the stream. `denoiser = "deepfilternet"`
  removes noticeably more noise and some reverb than the default ffmpeg denoiser.
- **No API key?** Set `trim.method = "heuristic"`. It finds the longest stretch
  of speaking-rate speech; congregational singing transcribes far slower than
  preaching. It works on most services, but check `sermonpod status` and fix any
  misses with `add --start/--end`.
- **Who preached.** If a service never says who is speaking, `unknown_preacher`
  decides what happens to it. `"include"` (the default) keeps it, which suits a
  regular pulpit preacher. `"exclude"` skips it, and you can add any it missed
  with `sermonpod add`. To also scan regular uploads, set
  `tabs = ["streams", "videos"]`.

## Development

```bash
pip install -e '.[dev]' && pytest
```

The tests use synthetic audio and a fake Claude client, so they need no network.
