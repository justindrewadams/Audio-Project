"""Command line entry point:  sermonpod <command> [options]"""

from __future__ import annotations

import argparse
import logging

from . import pipeline, youtube
from .config import load_config
from .state import State


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sermonpod", description=__doc__)
    p.add_argument("-c", "--config", default="config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="scan the channel and classify new videos")

    run = sub.add_parser("run", help="discover, process new sermons and rebuild the feed")
    run.add_argument("--no-discover", action="store_true", help="only process already-known videos")
    run.add_argument("--limit", type=int, help="process at most N videos (newest first)")
    run.add_argument("--keep-raw", action="store_true", help="keep the downloaded service audio")

    sub.add_parser("feed", help="rebuild feed.xml from finished episodes")
    sub.add_parser("status", help="list known videos and their status")

    add = sub.add_parser("add", help="force-include a video (e.g. one the matcher missed)")
    add.add_argument("video_id")
    add.add_argument("--start", type=float, help="sermon start in seconds (skips detection)")
    add.add_argument("--end", type=float, help="sermon end in seconds")

    retry = sub.add_parser("retry", help="re-queue a failed or rejected video")
    retry.add_argument("video_id")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(message)s")
    cfg = load_config(args.config)
    state = State(cfg.paths.state_file)

    if args.cmd == "discover":
        added = youtube.discover(cfg.source, state)
        print(f"{len(added)} new video(s) found")
    elif args.cmd == "run":
        pipeline.run(cfg, discover=not args.no_discover, limit=args.limit, keep_raw=args.keep_raw)
    elif args.cmd == "feed":
        print(pipeline.write_feed(cfg, state))
    elif args.cmd == "status":
        for v in sorted(state.videos.values(), key=lambda v: v.get("upload_date", "")):
            print(f"{v.get('upload_date', ''):8}  {v['status']:8}  {v['id']}  {v.get('title', '')[:60]}"
                  f"  ({v.get('reason', '')})")
    elif args.cmd == "add":
        if not state.get(args.video_id):
            info = youtube.video_details(cfg.source, args.video_id)
            if not info:
                raise SystemExit(f"could not fetch {args.video_id}")
            state.upsert(args.video_id, title=info.get("title") or "", description=info.get("description") or "",
                         upload_date=info.get("upload_date") or "", duration=info.get("duration") or 0,
                         thumbnail=info.get("thumbnail") or "",
                         url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={args.video_id}")
        fields = {"status": "matched", "reason": "added manually"}
        if args.start is not None and args.end is not None:
            fields.update(manual_start=args.start, manual_end=args.end)
        state.upsert(args.video_id, **fields)
        print(f"queued {args.video_id}; run `sermonpod run --no-discover` to process it")
    elif args.cmd == "retry":
        if not state.get(args.video_id):
            raise SystemExit(f"unknown video {args.video_id}")
        state.upsert(args.video_id, status="matched", reason="retry")
        print(f"re-queued {args.video_id}")


if __name__ == "__main__":
    main()
