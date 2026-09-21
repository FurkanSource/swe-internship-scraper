"""Concurrent, failure-isolated provider orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .dedupe import PotentialDuplicate, deduplicate_with_audit
from .filters import filter_jobs
from .models import Job, ProviderFailure, ScanResult
from .providers import Target, get_provider
from .providers.base import HttpClient, Provider
from .providers.http import RequestsJsonClient


@dataclass(frozen=True, slots=True)
class ScanDetails:
    """Scan result plus conservative, non-merging duplicate candidates."""

    result: ScanResult
    potential_duplicates: tuple[PotentialDuplicate, ...]


def scan_targets(
    targets: Iterable[Target],
    *,
    client: HttpClient | None = None,
    max_workers: int = 8,
    filter_swe: bool = True,
    locations: Iterable[str] = (),
    include_keywords: Iterable[str] = (),
    exclude_keywords: Iterable[str] = (),
) -> ScanResult:
    """Fetch targets concurrently while preserving board-level failures."""
    return scan_targets_detailed(
        targets,
        client=client,
        max_workers=max_workers,
        filter_swe=filter_swe,
        locations=locations,
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
    ).result


def scan_targets_detailed(
    targets: Iterable[Target],
    *,
    client: HttpClient | None = None,
    max_workers: int = 8,
    filter_swe: bool = True,
    locations: Iterable[str] = (),
    include_keywords: Iterable[str] = (),
    exclude_keywords: Iterable[str] = (),
) -> ScanDetails:
    """Fetch targets and retain uncertain duplicate pairs for optional auditing."""
    target_list = list(targets)
    http = client or RequestsJsonClient()
    jobs: list[Job] = []
    errors: list[ProviderFailure] = []

    resolved: dict[tuple[str, str, str], Provider] = {}
    for target in target_list:
        provider = get_provider(target.provider)
        validator = getattr(provider, "validate_target", None)
        if callable(validator):
            validator(target)
        resolved[(target.provider, target.name, target.slug)] = provider

    def fetch_one(target: Target) -> list[Job]:
        provider = resolved[(target.provider, target.name, target.slug)]
        return provider.fetch(target, http)

    workers = min(max(1, int(max_workers)), max(1, len(target_list)), 32)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, target): target for target in target_list}
        for future in as_completed(futures):
            target = futures[future]
            try:
                jobs.extend(future.result())
            except Exception as exc:
                errors.append(
                    ProviderFailure(
                        provider=target.provider,
                        company=target.name,
                        slug=target.slug,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

    if filter_swe:
        jobs = filter_jobs(
            jobs,
            locations=locations,
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
        )
    deduplication = deduplicate_with_audit(jobs)
    errors.sort(key=lambda error: (error.provider, error.company.casefold(), error.slug))
    return ScanDetails(
        ScanResult.from_iterables(deduplication.jobs, errors),
        deduplication.potential_duplicates,
    )
