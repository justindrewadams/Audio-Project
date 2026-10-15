"""Command line entry point:  sermonpod <command> [options]"""

from __future__ import annotations

import argparse
import logging

from . import pipeline, voice, youtube
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

    cov = sub.add_parser("cover", help="make 3000x3000 podcast cover art from a photo")
    cov.add_argument("photo")
    cov.add_argument("-o", "--output", default="cover.jpg")
    cov.add_argument("--focus", type=float, default=0.2,
                     help="for tall photos: which part to keep, 0 = top, 1 = bottom (default 0.2)")

    vp = sub.add_parser("voice", help="manage the preacher's voice profile")
    vsub = vp.add_subparsers(dest="voice_cmd", required=True)
    vsub.add_parser("status", help="show the profile and every video's voice score")
    yes = vsub.add_parser("yes", help="these videos ARE his sermons (teaches the profile)")
    yes.add_argument("video_ids", nargs="+")
    no = vsub.add_parser("no", help="these videos are NOT his sermons (teaches the profile)")
    no.add_argument("video_ids", nargs="+")
    vsub.add_parser("bootstrap", help="start the profile from the voice heard in most services")
    vsub.add_parser("recheck", help="re-score all processed videos with the current profile")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(message)s")
    if args.cmd == "cover":  # needs no config
        from pathlib import Path

        from .cover import make_cover
        print(make_cover(Path(args.photo), Path(args.output), focus_y=args.focus))
        return
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
            score = "" if v.get("voice_score") is None else f"  voice {v['voice_score']:.2f}"
            print(f"{v.get('upload_date', ''):8}  {v['status']:8}  {v['id']}  {v.get('title', '')[:50]}"
                  f"  ({v.get('reason', '')}){score}")
    elif args.cmd == "add":
        if not state.get(args.video_id):
            info = youtube.video_details(cfg.source, args.video_id)
            if not info:
                raise SystemExit(f"could not fetch {args.video_id}")
            state.upsert(args.video_id, title=info.get("title") or "", description=info.get("description") or "",
                         upload_date=info.get("upload_date") or "", duration=info.get("duration") or 0,
                         thumbnail=info.get("thumbnail") or "",
                         url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={args.video_id}")
        fields = {"status": "matched", "reason": "added manually", "forced": True}
        if args.start is not None and args.end is not None:
            fields.update(manual_start=args.start, manual_end=args.end)
        state.upsert(args.video_id, **fields)
        print(f"queued {args.video_id}; run `sermonpod run --no-discover` to process it")
    elif args.cmd == "retry":
        if not state.get(args.video_id):
            raise SystemExit(f"unknown video {args.video_id}")
        state.upsert(args.video_id, status="matched", reason="retry")
        print(f"re-queued {args.video_id}")
    elif args.cmd == "voice":
        voice_command(cfg, state, args)


def voice_command(cfg, state, args) -> None:
    if args.voice_cmd == "yes":
        pipeline.confirm(cfg, state, args.video_ids, is_him=True)
    elif args.voice_cmd == "no":
        pipeline.confirm(cfg, state, args.video_ids, is_him=False)
    elif args.voice_cmd == "bootstrap":
        group = pipeline.bootstrap_profile(cfg, state)
        if not group:
            print("no single voice dominates the processed services yet; process more, "
                  "or confirm a few with `sermonpod voice yes VIDEO_ID ...`")
            return
        print(f"enrolled {len(group)} sermons that share one voice:")
        for vid in group:
            print(f"  {vid}  {state.get(vid).get('title', '')}")
        print("if any of these aren't him, run `sermonpod voice no VIDEO_ID`")
    if args.voice_cmd in ("yes", "no", "bootstrap", "recheck"):
        for vid, old, new in pipeline.recheck(cfg, state):
            print(f"recheck: {vid}  {old} -> {new}  ({state.get(vid)['reason']})")
    if args.voice_cmd == "status":
        profile = voice.Profile(cfg.paths.voice_dir, cfg.voice)
        need = max(cfg.voice.min_sermons - len(profile.enrolled), 0)
        print(f"enrolled sermons: {len(profile.enrolled)}" + (f" (needs {need} more to start deciding)" if need else ""))
        print(f"known other speakers: {len(profile.impostors)}")
        if profile.ready:
            print(f"cutoff: {profile.threshold:.2f}  (unsure between {profile.threshold - cfg.voice.margin:.2f} "
                  f"and {profile.threshold:.2f})")
            print(f"calibration: {profile.stats}")
        print()
        for v in sorted(state.videos.values(), key=lambda v: v.get("upload_date", "")):
            if v.get("voice_score") is None and v["id"] not in profile.enrolled:
                continue
            tag = "enrolled" if v["id"] in profile.enrolled else ("other" if v["id"] in profile.impostors else "")
            score = "  -  " if v.get("voice_score") is None else f"{v['voice_score']:.2f}"
            print(f"{v.get('upload_date', ''):8}  {score}  {v['status']:8}  {tag:8}  {v['id']}  "
                  f"{v.get('title', '')[:50]}")


if __name__ == "__main__":
    main()
