"""Write an Apple/Spotify-compatible podcast RSS feed."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import Podcast

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
CONTENT = "http://purl.org/rss/1.0/modules/content/"
ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("itunes", ITUNES)
ET.register_namespace("content", CONTENT)
ET.register_namespace("atom", ATOM)


def _sub(parent, tag, text=None, /, **attrs):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _pub_date(upload_date: str) -> str:
    # YouTube gives YYYYMMDD; publish at noon UTC so the date is right in US timezones.
    dt = datetime.strptime(upload_date, "%Y%m%d").replace(hour=12, tzinfo=timezone.utc)
    return format_datetime(dt)


def episode_notes(ep: dict) -> str:
    parts = []
    if ep.get("summary"):
        parts.append(f"<p>{html.escape(ep['summary'])}</p>")
    if ep.get("scripture"):
        parts.append(f"<p>Scripture: {html.escape(', '.join(ep['scripture']))}</p>")
    parts.append(f'<p>Watch the full service: <a href="{html.escape(ep["url"])}">{html.escape(ep["url"])}</a></p>')
    return "".join(parts)


def build_feed(podcast: Podcast, episodes: list[dict], out_dir: Path) -> Path:
    """episodes: state entries with status 'done' (needs file, bytes, episode_duration)."""
    base = podcast.base_url.rstrip("/")
    rss = ET.Element("rss", {"version": "2.0"})
    ch = _sub(rss, "channel")
    _sub(ch, f"{{{ATOM}}}link", href=f"{base}/feed.xml", rel="self", type="application/rss+xml")
    _sub(ch, "title", podcast.title)
    _sub(ch, "link", base)
    _sub(ch, "description", podcast.description)
    _sub(ch, "language", podcast.language)
    _sub(ch, f"{{{ITUNES}}}author", podcast.author)
    _sub(ch, f"{{{ITUNES}}}summary", podcast.description)
    _sub(ch, f"{{{ITUNES}}}explicit", "true" if podcast.explicit else "false")
    _sub(ch, f"{{{ITUNES}}}type", "episodic")
    cat = _sub(ch, f"{{{ITUNES}}}category", text=podcast.category)
    if podcast.subcategory:
        _sub(cat, f"{{{ITUNES}}}category", text=podcast.subcategory)
    if podcast.owner_email:
        owner = _sub(ch, f"{{{ITUNES}}}owner")
        _sub(owner, f"{{{ITUNES}}}name", podcast.owner_name or podcast.author)
        _sub(owner, f"{{{ITUNES}}}email", podcast.owner_email)
    if podcast.image:
        img = f"{base}/{podcast.image}"
        _sub(ch, f"{{{ITUNES}}}image", href=img)
        image = _sub(ch, "image")
        _sub(image, "url", img)
        _sub(image, "title", podcast.title)
        _sub(image, "link", base)
    _sub(ch, "lastBuildDate", format_datetime(datetime.now(timezone.utc)))

    for ep in sorted(episodes, key=lambda e: e["upload_date"], reverse=True):
        item = _sub(ch, "item")
        title = ep.get("episode_title") or ep["title"]
        _sub(item, "title", title)
        _sub(item, f"{{{ITUNES}}}title", title)
        notes = episode_notes(ep)
        _sub(item, "description", notes)
        _sub(item, f"{{{CONTENT}}}encoded", notes)
        if ep.get("summary"):
            _sub(item, f"{{{ITUNES}}}summary", ep["summary"])
        _sub(item, "enclosure", url=f"{base}/{ep['file']}", length=ep["bytes"], type="audio/mpeg")
        _sub(item, "guid", f"youtube:{ep['id']}", isPermaLink="false")
        _sub(item, "pubDate", _pub_date(ep["upload_date"]))
        _sub(item, f"{{{ITUNES}}}duration", _hms(ep["episode_duration"]))
        _sub(item, f"{{{ITUNES}}}explicit", "true" if podcast.explicit else "false")
        _sub(item, "link", ep["url"])

    ET.indent(rss)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "feed.xml"
    ET.ElementTree(rss).write(path, encoding="utf-8", xml_declaration=True)
    return path
