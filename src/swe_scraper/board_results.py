"""Stable board identities for opt-in scan provenance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from .models import Job, JobSource
from .providers.base import Target


def target_key(target: Target) -> str:
    """Hash stable ATS coordinates, excluding names and scan tuning options."""
    origin = str(target.options.get("origin") or "").strip()
    if origin:
        parts = urlsplit(origin)
        origin = urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                parts.path.rstrip("/"),
                "",
                "",
            )
        )
    coordinates = (
        target.provider.strip().casefold(),
        target.slug.strip().casefold(),
        origin,
        str(target.options.get("tenant") or "").strip().casefold(),
        str(target.options.get("site") or "").strip().casefold(),
    )
    payload = json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BoardResult:
    target_key: str
    provider: str
    slug: str
    status: Literal["complete", "partial", "failed"]
    job_count: int
    sources: tuple[JobSource, ...]

    @classmethod
    def from_jobs(
        cls,
        target: Target,
        status: Literal["complete", "partial", "failed"],
        jobs: list[Job],
    ) -> BoardResult:
        sources = sorted(
            {source for job in jobs for source in job.sources},
            key=lambda source: (
                source.provider,
                source.namespace,
                source.source_job_id,
                source.application_url,
            ),
        )
        return cls(
            target_key(target),
            target.provider,
            target.slug,
            status,
            len(jobs),
            tuple(sources),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "target_key": self.target_key,
            "provider": self.provider,
            "slug": self.slug,
            "status": self.status,
            "job_count": self.job_count,
            "sources": [source.to_dict() for source in self.sources],
        }
