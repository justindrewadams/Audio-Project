"""A small phone-friendly status page published next to feed.xml."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

from .config import Podcast
from .feed import enclosure_url, service_label

CSS = """
:root { --bg:#faf8f5; --card:#fff; --text:#1d1b19; --muted:#6b655e; --line:#e7e2db;
        --accent:#7a4b1f; --warn:#9a5b00; --warnbg:#fff4e0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161412; --card:#211e1b; --text:#efe9e2; --muted:#a59d93; --line:#34302b;
          --accent:#e0a96d; --warn:#f0b35a; --warnbg:#2c2416; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width:640px; margin:0 auto; padding:24px 16px 48px; }
header { display:flex; gap:16px; align-items:center; margin-bottom:20px; }
header img { width:88px; height:88px; border-radius:12px; flex:none; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:28px 0 10px; }
.muted { color:var(--muted); font-size:14px; }
.buttons { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 4px; }
.btn { display:inline-block; padding:10px 14px; border-radius:10px; background:var(--accent);
       color:var(--bg); text-decoration:none; font-weight:600; font-size:15px; border:0; }
.btn.secondary { background:transparent; color:var(--accent); border:1px solid var(--line); }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:14px; margin-bottom:10px; }
.card h3 { font-size:16px; margin:0 0 2px; }
.card p { margin:6px 0 0; font-size:14px; }
audio { width:100%; margin-top:10px; }
.review { background:var(--warnbg); border-color:var(--warn); }
code { font-size:13px; background:var(--line); padding:1px 5px; border-radius:5px; }
details summary { cursor:pointer; color:var(--muted); font-size:14px; }
a { color:var(--accent); }
"""


def _e(text) -> str:
    return html.escape(str(text or ""))


def build_site(podcast: Podcast, videos: list[dict], out_dir: Path, manage_url: str = "") -> Path:
    base = podcast.base_url.rstrip("/")
    feed_url = f"{base}/feed.xml"
    apple = "podcasts://" + feed_url.split("://", 1)[1]
    by_date = sorted(videos, key=lambda v: v.get("upload_date", ""), reverse=True)
    done = [v for v in by_date if v.get("status") == "done"]
    review = [v for v in by_date if v.get("status") == "review"]
    failed = [v for v in by_date if v.get("status") == "failed"]
    skipped = [v for v in by_date if v.get("status") == "rejected"][:15]

    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width, initial-scale=1'>",
           f"<title>{_e(podcast.title)}</title><style>{CSS}</style></head><body><main>",
           "<header>"]
    if podcast.image:
        out.append(f"<img src='{_e(podcast.image)}' alt=''>")
    out.append(f"<div><h1>{_e(podcast.title)}</h1><div class='muted'>{len(done)} episodes · updated "
               f"{datetime.now(timezone.utc):%b %d, %Y}</div></div></header>")
    out.append("<div class='buttons'>"
               f"<a class='btn' href='{_e(apple)}'>Open in Apple Podcasts</a>"
               f"<a class='btn secondary' href='{_e(feed_url)}'>Feed URL</a>"
               + (f"<a class='btn secondary' href='{_e(manage_url)}'>Run / manage</a>" if manage_url else "")
               + "</div>")

    def card(v, extra_class="", body=""):
        title = v.get("episode_title") or v.get("title", "")
        label = service_label(v) if v.get("upload_date") else ""
        return (f"<div class='card {extra_class}'><h3>{_e(title)}</h3>"
                f"<div class='muted'>{_e(label)}</div>{body}</div>")

    def voice_note(v):
        if v.get("voice_score") is None:
            return ""
        return f" · voice match {v['voice_score']:.2f}"

    if review or failed:
        out.append("<h2>Needs your decision</h2>")
        out.append("<p class='muted'>Tap <b>Run / manage</b>, choose <b>Run workflow</b>, pick "
                   "<b>voice yes</b> (it's his sermon) or <b>voice no</b> (it isn't), and paste the "
                   "video ID.</p>")
        for v in review + failed:
            out.append(card(v, "review",
                            f"<p>{_e(v.get('reason'))}{_e(voice_note(v))}</p>"
                            f"<p>Video ID <code>{_e(v['id'])}</code> · "
                            f"<a href='{_e(v.get('url'))}'>watch on YouTube</a></p>"))

    out.append("<h2>Episodes</h2>")
    if not done:
        out.append("<p class='muted'>No episodes yet. They appear after Sunday's services are processed.</p>")
    for v in done:
        minutes = round(v.get("episode_duration", 0) / 60)
        body = (f"<p>{_e(v.get('summary'))}</p>" if v.get("summary") else "")
        body += f"<p class='muted'>{minutes} min{_e(voice_note(v))} · <a href='{_e(v.get('url'))}'>full service</a></p>"
        body += f"<audio controls preload='none' src='{_e(enclosure_url(podcast, v))}'></audio>"
        out.append(card(v, body=body))

    if skipped:
        out.append("<h2>Recently skipped</h2><details><summary>Show</summary>")
        for v in skipped:
            out.append(card(v, body=f"<p class='muted'>{_e(v.get('reason'))}{_e(voice_note(v))} · "
                                   f"<code>{_e(v['id'])}</code></p>"))
        out.append("</details>")

    out.append("</main></body></html>")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    path.write_text("\n".join(out))
    return path
