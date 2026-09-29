"""Deterministic exact identity grouping with similarity available for audit only."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .models import Job, JobSource, MatchEvidence
from .normalize import canonical_url, normalize_locations


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    reason: str
    confidence: float


@dataclass(frozen=True, slots=True)
class PotentialDuplicate:
    left_id: str
    right_id: str
    reason: str
    confidence: float


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    jobs: tuple[Job, ...]
    potential_duplicates: tuple[PotentialDuplicate, ...]


_LEGAL_SUFFIXES = {"co", "company", "corp", "corporation", "inc", "llc", "ltd"}
_LOCATION_ALIASES = {
    "nyc": "new york ny",
    "new york city": "new york ny",
    "new york new york": "new york ny",
    "sf": "san francisco ca",
    "washington d c": "washington dc",
}
_COUNTRY_PATTERNS = {
    "us": re.compile(r"\b(?:us|usa|u s(?: a)?|united states(?: of america)?)\b"),
    "gb": re.compile(r"\b(?:uk|u k|gb|g b|great britain|united kingdom)\b"),
}


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _company_key(value: str) -> str:
    words = _words(value)
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _title_words(value: str) -> tuple[str, ...]:
    aliases = {
        "engineering": "engineer",
        "engineers": "engineer",
        "developer": "engineer",
        "developers": "engineer",
        "interns": "intern",
        "internship": "intern",
    }
    words = [aliases.get(word, word) for word in _words(value)]
    return tuple(sorted(words))


def _title_key(value: str) -> str:
    return " ".join(_title_words(value))


def _location_key(value: str) -> str:
    key = " ".join(_words(value))
    return _LOCATION_ALIASES.get(key, key)


def _country_keys(job: Job) -> set[str]:
    return {
        country
        for location in job.locations
        for country, pattern in _COUNTRY_PATTERNS.items()
        if pattern.search(_location_key(location))
    }


def _location_keys(job: Job) -> set[str]:
    keys = {_location_key(location) for location in job.locations if location}
    if job.remote or any("remote" in key.split() for key in keys):
        countries = _country_keys(job)
        if countries:
            keys.discard("remote")
            keys.update(f"remote:{country}" for country in countries)
        else:
            keys.add("remote")
    return keys


def identity_key(job: Job) -> tuple[str, str]:
    canonical = canonical_url(job.application_url)
    if canonical:
        return "url", canonical
    # Invalid URLs must not fall back to a provider-wide, unscoped source ID.
    scoped = sorted(key for key in _exact_keys(job) if key[0] == "source")
    if scoped:
        return "source", json.dumps(scoped[0][1:], separators=(",", ":"))
    return "record", json.dumps(
        (job.provider, job.id, job.application_url), separators=(",", ":")
    )


def _exact_keys(job: Job) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    if url := canonical_url(job.application_url):
        keys.add(("url", url))
    # Existing provenance is authoritative. Never infer its namespace from a
    # primary record's metadata, employer name, or URL.
    for source in job.sources:
        if url := canonical_url(source.application_url):
            keys.add(("url", url))
        if source.namespace.strip():
            keys.add(
                (
                    "source",
                    source.provider.casefold(),
                    source.namespace,
                    source.source_job_id,
                )
            )
    return keys


def _exact_match(existing: Job, incoming: Job) -> DuplicateMatch | None:
    shared = _exact_keys(existing) & _exact_keys(incoming)
    if any(key[0] == "url" for key in shared):
        return DuplicateMatch("exact_url", 1.0)
    if shared:
        return DuplicateMatch("source_identity", 1.0)
    return None


def duplicate_match(existing: Job, incoming: Job) -> DuplicateMatch | None:
    """Return evidence only when two records are safe to merge automatically."""
    return _exact_match(existing, incoming)


def _potential_match(left: Job, right: Job) -> PotentialDuplicate | None:
    company = _company_key(left.company)
    if not company or company != _company_key(right.company):
        return None
    left_countries, right_countries = _country_keys(left), _country_keys(right)
    if left_countries and right_countries and left_countries.isdisjoint(right_countries):
        return None
    left_title = set(_title_words(left.title))
    right_title = set(_title_words(right.title))
    if not left_title or not right_title:
        return None
    overlap = len(left_title & right_title) / len(left_title | right_title)
    if overlap < 0.25 or not (_location_keys(left) & _location_keys(right)):
        return None
    confidence = round(min(0.92, 0.60 + overlap * 0.3), 2)
    return PotentialDuplicate(
        left_id=left.id,
        right_id=right.id,
        reason="similar_company_title_location",
        confidence=confidence,
    )


def _job_sort_key(job: Job) -> tuple[object, ...]:
    return (
        _company_key(job.company),
        _title_key(job.title),
        tuple(sorted(_location_keys(job))),
        job.provider.casefold(),
        job.source_job_id,
        canonical_url(job.application_url),
        job.id,
        json.dumps(job.to_dict(), sort_keys=True, default=str),
    )


def _source_key(source: JobSource) -> tuple[str, ...]:
    return (
        source.provider.casefold(),
        source.namespace,
        source.source_job_id,
        canonical_url(source.application_url),
        source.provider,
        source.application_url,
    )


def _primary_key(job: Job) -> tuple[object, ...]:
    return (
        bool(job.description),
        len(job.description),
        bool(job.posted_at),
        len(job.locations),
        job.provider.casefold(),
        job.source_job_id,
        canonical_url(job.application_url),
        _job_sort_key(job),
    )


def merge_jobs(
    existing: Job,
    incoming: Job,
    match: DuplicateMatch | None = None,
    *,
    primary: Job | None = None,
) -> Job:
    if primary is None:
        primary = max((existing, incoming), key=_primary_key)
    locations = normalize_locations((*existing.locations, *incoming.locations))
    metadata = dict(existing.metadata)
    metadata.update(incoming.metadata)
    metadata.pop("source_namespace", None)
    metadata.update(primary.metadata)
    sources = tuple(
        sorted(
            {source for value in (existing, incoming) for source in value.sources},
            key=_source_key,
        )
    )
    evidence = list(existing.merge_evidence) + list(incoming.merge_evidence)
    if match is not None:
        existing_keys = {_source_key(item) for item in existing.sources}
        incoming_source = next(
            (
                source
                for source in incoming.sources
                if _source_key(source) not in existing_keys
            ),
            incoming.sources[0],
        )
        evidence.append(MatchEvidence(match.reason, match.confidence, incoming_source))
    evidence_by_key = {
        (value.reason, value.confidence, _source_key(value.source)): value
        for value in evidence
    }
    merged_evidence = tuple(
        evidence_by_key[key]
        for key in sorted(evidence_by_key, key=lambda item: (item[0], item[1], item[2]))
    )
    if match is not None:
        metadata["deduplication"] = {
            "reason": match.reason,
            "confidence": match.confidence,
            "providers": sorted({source.provider for source in sources}),
            "source_job_ids": sorted({source.source_job_id for source in sources}),
            "matches": [
                {
                    "provider": value.source.provider,
                    "source_job_id": value.source.source_job_id,
                    "namespace": value.source.namespace,
                    "reason": value.reason,
                    "confidence": value.confidence,
                }
                for value in merged_evidence
            ],
        }
    descriptions = sorted(
        {value for value in (existing.description, incoming.description) if value},
        key=lambda value: (len(value), value),
    )
    posted = sorted(value for value in (existing.posted_at, incoming.posted_at) if value)
    return replace(
        primary,
        locations=locations,
        description=descriptions[-1] if descriptions else "",
        posted_at=posted[0] if posted else "",
        remote=existing.remote or incoming.remote,
        metadata=metadata,
        sources=sources,
        merge_evidence=merged_evidence,
    )


def _exact_components(jobs: Iterable[Job]) -> list[Job]:
    """Union every existing component reached by a record's exact identities."""
    groups: dict[int, Job] = {}
    primaries: dict[int, Job] = {}
    owners: dict[tuple[str, ...], int] = {}
    for index, job in enumerate(sorted(jobs, key=_job_sort_key)):
        matches = sorted({owners[key] for key in _exact_keys(job) if key in owners})
        root = matches[0] if matches else index
        primary = job
        if matches:
            first = groups[root]
            primary = max((primaries[root], primary), key=_primary_key)
            job = merge_jobs(first, job, _exact_match(first, job), primary=primary)
            for other in matches[1:]:
                member = groups.pop(other)
                primary = max((primary, primaries.pop(other)), key=_primary_key)
                job = merge_jobs(job, member, _exact_match(job, member), primary=primary)
        groups[root] = job
        # Rank original records, not the increasingly enriched group aggregate.
        primaries[root] = primary
        for key in _exact_keys(job):
            owners[key] = root
    return sorted(groups.values(), key=_job_sort_key)


def deduplicate(jobs: Iterable[Job]) -> list[Job]:
    """Merge only connected canonical URL or explicitly scoped source identities."""
    return _exact_components(jobs)


def deduplicate_with_audit(jobs: Iterable[Job]) -> DeduplicationResult:
    merged = deduplicate(jobs)
    potentials: list[PotentialDuplicate] = []
    company_indices: dict[str, list[int]] = defaultdict(list)
    for index, job in enumerate(merged):
        company_indices[_company_key(job.company)].append(index)
    for index, left in enumerate(merged):
        for right_index in company_indices[_company_key(left.company)]:
            if right_index <= index:
                continue
            right = merged[right_index]
            candidate = _potential_match(left, right)
            if candidate is not None:
                potentials.append(candidate)
    return DeduplicationResult(tuple(merged), tuple(potentials))
