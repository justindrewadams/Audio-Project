"""Try each of yt-dlp's YouTube client types against the channel's newest
livestream and report which ones YouTube lets through from this computer."""

import os
import sys

import yt_dlp

channel = sys.argv[1].rstrip("/")
base = {"quiet": True, "no_warnings": True}
if os.environ.get("SERMONPOD_PROXY"):
    base["proxy"] = os.environ["SERMONPOD_PROXY"]
    print("using the proxy from the YT_PROXY secret")
with yt_dlp.YoutubeDL({**base, "extract_flat": "in_playlist", "playlist_items": "1-3"}) as ydl:
    ids = [e["id"] for e in ydl.extract_info(f"{channel}/streams", download=False)["entries"]]
print("newest livestreams:", ids)

clients = ["default", "tv", "tv_simply", "tv_embedded", "web_safari", "web_embedded",
           "mweb", "ios", "android", "android_vr"]
for client in clients:
    opts = dict(base, format="bestaudio/best")
    if client != "default":
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={ids[0]}", download=False)
        print(f"OK    {client:13} title={info.get('title')!r} audio_format={info.get('format_id')}")
    except Exception as e:
        print(f"FAIL  {client:13} {str(e).splitlines()[0][:110]}")
