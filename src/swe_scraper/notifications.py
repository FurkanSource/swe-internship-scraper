"""Notification interfaces for newly discovered jobs."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .models import Job


class Notifier(Protocol):
    def notify(self, jobs: Sequence[Job]) -> None: ...


class JsonLinesNotifier:
    """Append portable discovery events for downstream notification systems."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def notify(self, jobs: Sequence[Job]) -> None:
        if not jobs:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "event": "jobs.discovered",
            "generated_at": dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "job_count": len(jobs),
            "jobs": [job.to_dict() for job in jobs],
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
