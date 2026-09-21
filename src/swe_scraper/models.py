"""Public data contracts for scraper providers and exports."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class JobSource:
    """Stable identity and direct URL for one source record."""

    provider: str
    source_job_id: str
    application_url: str

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.source_job_id.strip():
            raise ValueError("job source requires provider and source_job_id")
        if not self.application_url.startswith(("http://", "https://")):
            raise ValueError("job source application_url must be HTTP(S)")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> JobSource:
        return cls(
            provider=str(raw.get("provider") or "").strip(),
            source_job_id=str(raw.get("source_job_id") or "").strip(),
            application_url=str(raw.get("application_url") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    """Auditable reason that an additional source was merged into a job."""

    reason: str
    confidence: float
    source: JobSource

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("match evidence requires a reason")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("match evidence confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "confidence": float(self.confidence),
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> MatchEvidence:
        source = raw.get("source")
        if not isinstance(source, Mapping):
            source = {
                "provider": raw.get("provider"),
                "source_job_id": raw.get("source_job_id"),
                "application_url": raw.get("application_url"),
            }
        return cls(
            reason=str(raw.get("reason") or "").strip(),
            confidence=float(raw.get("confidence") or 0.0),
            source=JobSource.from_mapping(source),
        )


@dataclass(frozen=True, slots=True)
class Job:
    """A provider-neutral job posting returned by an official ATS."""

    id: str
    company: str
    title: str
    application_url: str
    provider: str
    source_job_id: str
    locations: tuple[str, ...] = ()
    posted_at: str = ""
    description: str = ""
    remote: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    sources: tuple[JobSource, ...] = ()
    merge_evidence: tuple[MatchEvidence, ...] = ()

    def __post_init__(self) -> None:
        required = {
            "id": self.id,
            "company": self.company,
            "title": self.title,
            "application_url": self.application_url,
            "provider": self.provider,
            "source_job_id": self.source_job_id,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"job fields must be non-empty: {', '.join(missing)}")
        if not self.application_url.startswith(("http://", "https://")):
            raise ValueError("application_url must be an HTTP(S) URL")
        if not self.sources:
            object.__setattr__(
                self,
                "sources",
                (
                    JobSource(
                        provider=self.provider,
                        source_job_id=self.source_job_id,
                        application_url=self.application_url,
                    ),
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["locations"] = list(self.locations)
        data["metadata"] = dict(self.metadata)
        data["sources"] = [source.to_dict() for source in self.sources]
        data["merge_evidence"] = [value.to_dict() for value in self.merge_evidence]
        return data

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Job:
        locations = raw.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        raw_sources = raw.get("sources") or []
        sources = tuple(
            JobSource.from_mapping(value)
            for value in raw_sources
            if isinstance(value, Mapping)
        )
        raw_evidence = raw.get("merge_evidence") or []
        evidence = tuple(
            MatchEvidence.from_mapping(value)
            for value in raw_evidence
            if isinstance(value, Mapping)
        )
        return cls(
            id=str(raw.get("id") or "").strip(),
            company=str(raw.get("company") or raw.get("company_name") or "").strip(),
            title=str(raw.get("title") or raw.get("role") or "").strip(),
            application_url=str(raw.get("application_url") or raw.get("url") or "").strip(),
            provider=str(raw.get("provider") or raw.get("source") or "unknown").strip(),
            source_job_id=str(raw.get("source_job_id") or raw.get("id") or "").strip(),
            locations=tuple(
                str(value).strip() for value in locations if str(value).strip()
            ),
            posted_at=str(raw.get("posted_at") or "").strip(),
            description=str(raw.get("description") or ""),
            remote=bool(raw.get("remote", False)),
            metadata=dict(raw.get("metadata") or {}),
            sources=sources,
            merge_evidence=evidence,
        )


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    provider: str
    company: str
    slug: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScanResult:
    jobs: tuple[Job, ...]
    errors: tuple[ProviderFailure, ...] = ()
    generated_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )

    @classmethod
    def from_iterables(
        cls,
        jobs: Iterable[Job],
        errors: Iterable[ProviderFailure] = (),
    ) -> ScanResult:
        return cls(tuple(jobs), tuple(errors))

    def to_dict(self) -> dict[str, Any]:
        providers = sorted({job.provider for job in self.jobs})
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "job_count": len(self.jobs),
            "error_count": len(self.errors),
            "providers": providers,
            "jobs": [job.to_dict() for job in self.jobs],
            "errors": [error.to_dict() for error in self.errors],
        }
