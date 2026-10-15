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

## Running it from your phone (GitHub, no computer needed)

The workflow in `.github/workflows/sermonpod.yml` runs every Sunday night on
GitHub's servers. It checks the Live tab for that day's AM and PM services,
skips the classes, and adds the sermons to the podcast. You'll find:

- **Episodes** in Apple Podcasts, as soon as you've subscribed once
- **A status page** at https://justindrewadams.github.io/Audio-Project, with a
  subscribe button, players, and anything waiting for your decision
- **Controls** in the GitHub app under Actions → Sermon podcast → Run workflow:
  `voice yes` / `voice no` / `add` / `retry` / `backfill`, plus a video ID

### One-time setup (all doable in Safari on your phone)

1. **Make the repo public.** In Settings → General → Danger Zone, choose
   *Change visibility* → Public. Podcast apps can only download episodes from a
   public repo. The code holds nothing secret, and your keys stay in encrypted
   secrets.
2. **Turn on Pages.** In Settings → Pages → Source, choose **GitHub Actions**.
3. **Add your Claude key.** In Settings → Secrets and variables → Actions →
   *New repository secret*, name it `ANTHROPIC_API_KEY`.
4. **Add an email for the feed.** In the same place, on the *Variables* tab, add
   `OWNER_EMAIL`. Apple and Spotify send ownership confirmations to it.
5. **Run it once.** In Actions → Sermon podcast → **Run workflow** → `run`.
   The first run goes back to July 1 (`since` in `cloud/config.toml`) and does
   up to 8 services. Use `backfill` to catch up faster.
6. **Subscribe.** On the status page, tap **Open in Apple Podcasts**. To list it
   publicly in Apple Podcasts or Spotify, submit the feed URL once in Apple
   Podcasts Connect or Spotify for Creators.

GitHub emails you, and notifies you in its app, if a run fails.

**Cover art with his photo.** In the repo, tap *Add file → Upload files*, upload
the photo, and name it `photo.jpg` inside the `cloud` folder (`cloud/photo.jpg`).
The next run turns it into 3000×3000 cover art: the photo full-bleed, with
"SERMONS / Joshua Adams / Mountain Home church of Christ" set over a dark
fade at the bottom. On a computer, `sermonpod cover photo.jpg` makes the
same image locally.

**If YouTube blocks GitHub's servers** (the run fails with "found no videos" or
"Sign in to confirm you're not a bot"), add your YouTube cookies. On any
computer, sign in to YouTube and export cookies with a "Get cookies.txt"
browser extension. Paste the file's contents into a secret named
`YT_COOKIES`. This needs a computer once; after that it runs on its own.

## Running it on a computer


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
