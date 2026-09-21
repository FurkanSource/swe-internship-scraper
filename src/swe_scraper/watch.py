"""State handling for recurring scans."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .dedupe import identity_key
from .models import Job


def load_seen(path: Path | str) -> set[str]:
    target = Path(path)
    if not target.exists():
        return set()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(value) for value in data if isinstance(value, str)}


def key_text(job: Job) -> str:
    kind, value = identity_key(job)
    return f"{kind}:{value}"


def unseen_jobs(jobs: tuple[Job, ...] | list[Job], seen: set[str]) -> list[Job]:
    return [job for job in jobs if key_text(job) not in seen]


def save_seen(path: Path | str, jobs: tuple[Job, ...] | list[Job]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = load_seen(target)
    existing.update(key_text(job) for job in jobs)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(sorted(existing), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
