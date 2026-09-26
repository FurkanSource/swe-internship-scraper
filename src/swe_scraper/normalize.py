"""Normalization helpers shared by providers and exporters."""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from collections.abc import Iterable
from typing import Any

TRACKING_QUERY_KEYS = {
    "gh_src",
    "source",
    "ref",
    "referer",
    "referrer",
    "src",
    "trk",
    "from",
    "fbclid",
    "gclid",
    "_hsenc",
    "_hsmi",
    "mc_cid",
    "mc_eid",
    "tracking",
    "trackingid",
    "lever-source",
    "lever-origin",
}


def canonical_url(value: object) -> str:
    """Return an HTTP(S) URL with tracking data and cosmetic variance removed."""
    raw = str(value or "").strip()
    if not raw or any(ord(character) < 32 for character in raw):
        return ""
    try:
        parts = urllib.parse.urlsplit(raw)
        port = parts.port
    except (UnicodeError, ValueError):
        return ""
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower().rstrip(".")
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = host if port is None or default_port else f"{host}:{port}"
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        netloc = "job-boards.greenhouse.io"
    path = parts.path.rstrip("/") or "/"
    query = []
    for key, value_item in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        if lowered == "gh_jid" and re.search(r"/jobs/\d+", path):
            continue
        query.append((key, value_item))
    query.sort()
    return urllib.parse.urlunsplit(
        (scheme, netloc, path, urllib.parse.urlencode(query, doseq=True), "")
    )


def normalize_locations(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable):
        return ()
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split())
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            normalized.append(clean)
    return tuple(normalized)


def iso_datetime(value: Any) -> str:
    """Normalize ISO strings or epoch milliseconds/seconds without inventing dates."""
    if value in (None, ""):
        return ""
    try:
        # Eight-digit strings are calendar dates; numeric values remain epochs.
        if isinstance(value, str) and re.fullmatch(r"[0-9]{8}", value.strip()):
            return (
                dt.datetime.strptime(value.strip(), "%Y%m%d")
                .replace(tzinfo=dt.timezone.utc)
                .isoformat()
            )
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            stamp = float(value)
            if stamp > 10_000_000_000:
                stamp /= 1000
            return (
                dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
                .replace(microsecond=0)
                .isoformat()
            )
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    except (OSError, OverflowError, TypeError, ValueError):
        return ""
