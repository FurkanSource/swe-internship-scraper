"""Recover completed boards from an interrupted full-catalog scan."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import SCHEMA_VERSION, Job
from .providers.base import Target


def _default_cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "swe-scraper" / "checkpoints"


def _target_data(target: Target) -> dict[str, Any]:
    return {
        "provider": target.provider,
        "name": target.name,
        "slug": target.slug,
        "options": dict(target.options),
    }


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ScanCheckpointStore:
    """One scan's board results, scoped to its output and exact inputs."""

    def __init__(
        self,
        *,
        output: Path,
        targets: Sequence[Target],
        scan_options: Mapping[str, Any],
        version: str,
        resume: bool,
        cache_root: Path | None = None,
    ) -> None:
        root = cache_root if cache_root is not None else _default_cache_root()
        output_key = os.path.normcase(str(output.resolve()))
        self.path = root / hashlib.sha256(output_key.encode("utf-8")).hexdigest()[:24]
        inputs = {
            "targets": [_target_data(target) for target in targets],
            "scan_options": dict(scan_options),
            "version": version,
            "schema_version": SCHEMA_VERSION,
        }
        encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        manifest_path = self.path / "manifest.json"
        if resume:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict) or not isinstance(
                    manifest.get("run_id"), str
                ):
                    raise ValueError("invalid checkpoint manifest")
                created_at = datetime.fromisoformat(manifest["created_at"])
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise ValueError("no valid checkpoint to resume") from exc
            if manifest.get("fingerprint") != fingerprint:
                raise ValueError("checkpoint does not match this scan configuration")
            age = datetime.now(timezone.utc) - created_at if created_at.tzinfo else None
            if age is None or age > timedelta(hours=24) or age < -timedelta(minutes=5):
                raise ValueError("checkpoint is older than 24 hours; start a fresh scan")
            self.run_id = manifest["run_id"]
        else:
            self.path.mkdir(parents=True, exist_ok=True)
            for child in self.path.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
            self.run_id = uuid.uuid4().hex
            _atomic_json(
                manifest_path,
                {
                    "fingerprint": fingerprint,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": self.run_id,
                },
            )

    def load(self, index: int, target: Target) -> list[Job] | None:
        try:
            value = json.loads((self.path / f"{index}.json").read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("run_id") != self.run_id
                or value.get("index") != index
                or value.get("target") != _target_data(target)
                or not isinstance(value.get("jobs"), list)
                or value.get("jobs_sha256") != _digest(value["jobs"])
            ):
                return None
            rows = value["jobs"]
            if any(not isinstance(row, dict) for row in rows):
                return None
            return [Job.from_mapping(row) for row in rows]
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save(self, index: int, target: Target, jobs: list[Job]) -> None:
        rows = [job.to_dict() for job in jobs]
        _atomic_json(
            self.path / f"{index}.json",
            {
                "run_id": self.run_id,
                "index": index,
                "target": _target_data(target),
                "jobs": rows,
                "jobs_sha256": _digest(rows),
            },
        )

    def clear(self) -> None:
        if not self.path.exists():
            return
        for child in self.path.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
        self.path.rmdir()
