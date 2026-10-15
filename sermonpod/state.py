"""Tiny JSON store tracking each video's progress through the pipeline.

Status flow:  matched | unnamed  ->  downloaded  ->  transcribed  ->  done
              rejected / failed are terminal until reset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.videos: dict[str, dict] = {}
        if self.path.exists():
            self.videos = json.loads(self.path.read_text())

    def get(self, video_id: str) -> dict | None:
        return self.videos.get(video_id)

    def upsert(self, video_id: str, **fields) -> dict:
        entry = self.videos.setdefault(video_id, {"id": video_id})
        entry.update(fields)
        self.save()
        return entry

    def with_status(self, *statuses: str) -> list[dict]:
        return [v for v in self.videos.values() if v.get("status") in statuses]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.videos, indent=2, sort_keys=True))
        os.replace(tmp, self.path)
