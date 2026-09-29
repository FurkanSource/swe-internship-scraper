"""Bounded, best-effort disk caching for public provider detail payloads.

Callers must fetch current listings every time and key each detail by provider,
detail URL, options, and a digest of the FULL current listing row. Do not pass
credentials, authentication state, or listing responses as payloads. Only the
key hash is stored. Description-only changes with unchanged listing rows can
remain stale until TTL expiry; a miss never returns expired data.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__

MAX_ENTRY_BYTES = 10 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_CACHE_ENTRIES = 4096
_SCHEMA_VERSION = 1
_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class DetailCacheEntry:
    """A JSON payload snapshot and its original UTC fetch timestamp."""

    payload: Any
    fetched_at: str


def _default_cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "swe-scraper" / "details"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DetailCache:
    """TTL cache with atomic writes, 4096 files, and 128 MiB of completed entries.

    A single encoded entry, including its envelope, is limited to 10 MiB.

    The now callable must return an aware datetime. Save timestamps the freshly
    fetched payload at invocation, so call it immediately after fetching.
    Hits preserve that timestamp and never renew their TTL. Invalid JSON
    payloads raise; invalid keys, oversized entries, and disk failures skip
    caching and return the fresh entry. Save results are detached JSON snapshots.

    The limits cover owned completed JSON files, not unrelated files or an
    in-progress UUID temporary. Writes are serialized within this process;
    independent processes and disk failures can exceed aggregate limits until
    a successful prune. Expired files remain on disk until bounded eviction.
    No background cleanup or cross-process lock is introduced.
    """

    def __init__(
        self,
        root: Path | None = None,
        ttl_seconds: int = 900,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be an integer between 1 and 3600")
        self.root = root if root is not None else _default_cache_root()
        self.ttl_seconds = ttl_seconds
        self._now = now

    def _instant(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _key_digest(self, key: object) -> str:
        return _digest(
            {"key": key, "version": __version__, "schema_version": _SCHEMA_VERSION}
        )

    def load(self, key: object) -> DetailCacheEntry | None:
        """Return an intact, unexpired entry; every invalid state is a miss."""
        try:
            key_sha256 = self._key_digest(key)
            path = self.root / f"{key_sha256}.json"
            if path.is_symlink():
                return None
            with path.open("rb") as stream:
                encoded = stream.read(MAX_ENTRY_BYTES + 1)
            if len(encoded) > MAX_ENTRY_BYTES:
                return None
            record = json.loads(encoded)
            if (
                not isinstance(record, dict)
                or type(record.get("schema_version")) is not int
                or record.get("schema_version") != _SCHEMA_VERSION
                or record.get("version") != __version__
                or record.get("key_sha256") != key_sha256
                or "payload" not in record
                or not isinstance(record.get("fetched_at"), str)
                or record.get("payload_sha256") != _digest(record["payload"])
            ):
                return None
            fetched_at = record["fetched_at"]
            fetched = datetime.fromisoformat(fetched_at)
            if fetched.tzinfo is None or fetched.utcoffset() != timedelta(0):
                return None
            age = (self._instant() - fetched).total_seconds()
            if not 0 <= age < self.ttl_seconds:
                return None
            return DetailCacheEntry(payload=record["payload"], fetched_at=fetched_at)
        except (OSError, ValueError, TypeError, RecursionError):
            return None

    def save(self, key: object, payload: object) -> DetailCacheEntry:
        """Return fresh data even when persistence fails; never read an old fallback."""
        payload_bytes = _canonical(payload)
        entry = DetailCacheEntry(
            payload=json.loads(payload_bytes), fetched_at=self._instant().isoformat()
        )
        try:
            key_sha256 = self._key_digest(key)
        except (TypeError, ValueError, RecursionError):
            return entry
        encoded = _canonical(
            {
                "schema_version": _SCHEMA_VERSION,
                "version": __version__,
                "key_sha256": key_sha256,
                "payload": entry.payload,
                "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "fetched_at": entry.fetched_at,
            }
        )
        if len(encoded) > min(MAX_ENTRY_BYTES, MAX_CACHE_BYTES):
            return entry
        path = self.root / f"{key_sha256}.json"
        temporary = self.root / f".{path.name}.{uuid.uuid4().hex}.tmp"
        with _WRITE_LOCK:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                with temporary.open("xb") as stream:
                    stream.write(encoded)
                os.replace(temporary, path)
                self._prune(path)
            except OSError:
                pass
            finally:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return entry

    def _prune(self, newest: Path) -> None:
        """Evict oldest writes; hits do not renew retention or freshness."""
        entries: list[tuple[str, int, int]] = []
        # DirEntry metadata avoids a separate filesystem stat for every entry
        # on Windows. Only sort when eviction is actually necessary.
        with os.scandir(self.root) as children:
            for child in children:
                stem = child.name.removesuffix(".json")
                if (
                    not child.name.endswith(".json")
                    or len(stem) != 64
                    or any(c not in "0123456789abcdef" for c in stem)
                ):
                    continue
                try:
                    if not child.is_file(follow_symlinks=False):
                        continue
                    stat = child.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append((child.name, stat.st_size, stat.st_mtime_ns))
        total = sum(item[1] for item in entries)
        count = len(entries)
        if count <= MAX_CACHE_ENTRIES and total <= MAX_CACHE_BYTES:
            return
        entries.sort(key=lambda item: (item[0] == newest.name, item[2], item[0]))
        for name, size, _ in entries:
            if count <= MAX_CACHE_ENTRIES and total <= MAX_CACHE_BYTES:
                break
            try:
                (self.root / name).unlink(missing_ok=True)
            except OSError:
                continue
            total -= size
            count -= 1
