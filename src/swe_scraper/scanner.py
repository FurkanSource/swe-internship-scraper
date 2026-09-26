"""Concurrent, failure-isolated provider orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from time import monotonic
from typing import cast

from .checkpoints import ScanCheckpointStore
from .dedupe import PotentialDuplicate, deduplicate, deduplicate_with_audit
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


@dataclass(frozen=True, slots=True)
class ScanProgress:
    completed: int
    total: int
    resumed: int
    failed: int
    fetched_records: int
    elapsed_seconds: float


def scan_targets(
    targets: Iterable[Target],
    *,
    client: HttpClient | None = None,
    max_workers: int = 8,
    filter_swe: bool = True,
    include_adjacent: bool = False,
    locations: Iterable[str] = (),
    include_keywords: Iterable[str] = (),
    exclude_keywords: Iterable[str] = (),
    progress_callback: Callable[[ScanProgress], None] | None = None,
    checkpoint_store: ScanCheckpointStore | None = None,
) -> ScanResult:
    """Fetch targets concurrently while preserving board-level failures."""
    return scan_targets_detailed(
        targets,
        client=client,
        max_workers=max_workers,
        filter_swe=filter_swe,
        include_adjacent=include_adjacent,
        locations=locations,
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
        progress_callback=progress_callback,
        checkpoint_store=checkpoint_store,
        audit=False,
    ).result


def scan_targets_detailed(
    targets: Iterable[Target],
    *,
    client: HttpClient | None = None,
    max_workers: int = 8,
    filter_swe: bool = True,
    include_adjacent: bool = False,
    locations: Iterable[str] = (),
    include_keywords: Iterable[str] = (),
    exclude_keywords: Iterable[str] = (),
    progress_callback: Callable[[ScanProgress], None] | None = None,
    checkpoint_store: ScanCheckpointStore | None = None,
    audit: bool = True,
) -> ScanDetails:
    """Fetch targets and retain uncertain duplicate pairs for optional auditing."""
    target_list = list(targets)
    http = client or RequestsJsonClient()
    jobs: list[Job] = []
    errors: list[ProviderFailure] = []
    started = monotonic()
    completed = 0
    resumed = 0

    def report_progress() -> None:
        if progress_callback is not None:
            progress_callback(
                ScanProgress(
                    completed=completed,
                    total=len(target_list),
                    resumed=resumed,
                    failed=len(errors),
                    fetched_records=len(jobs),
                    elapsed_seconds=monotonic() - started,
                )
            )

    resolved: dict[tuple[str, str, str], Provider] = {}
    for target in target_list:
        provider = get_provider(target.provider)
        validator = getattr(provider, "validate_target", None)
        if callable(validator):
            validator(target)
        resolved[(target.provider, target.name, target.slug)] = provider

    def fetch_one(target: Target) -> list[Job]:
        provider = resolved[(target.provider, target.name, target.slug)]
        candidate_fetch = getattr(provider, "fetch_candidates", None)
        if filter_swe and callable(candidate_fetch):
            return cast(list[Job], candidate_fetch(target, http))
        return provider.fetch(target, http)

    pending_targets: list[tuple[int, Target]] = []
    for index, target in enumerate(target_list):
        cached = checkpoint_store.load(index, target) if checkpoint_store else None
        if cached is None:
            pending_targets.append((index, target))
        else:
            jobs.extend(cached)
            resumed += 1
            completed += 1
    report_progress()

    workers = min(max(1, int(max_workers)), max(1, len(target_list)), 32)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_one, target): (index, target)
            for index, target in pending_targets
        }
        waiting = set(futures)
        while waiting:
            done, waiting = wait(waiting, timeout=10, return_when=FIRST_COMPLETED)
            for future in done:
                index, target = futures[future]
                try:
                    fetched = future.result()
                except Exception as exc:
                    errors.append(
                        ProviderFailure(
                            provider=target.provider,
                            company=target.name,
                            slug=target.slug,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                else:
                    if checkpoint_store:
                        checkpoint_store.save(index, target, fetched)
                    jobs.extend(fetched)
                completed += 1
                report_progress()
            if not done:
                report_progress()

    jobs = filter_jobs(
        jobs,
        filter_swe=filter_swe,
        include_adjacent=include_adjacent,
        locations=locations,
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
    )
    if audit:
        deduplication = deduplicate_with_audit(jobs)
        unique_jobs = deduplication.jobs
        potentials = deduplication.potential_duplicates
    else:
        unique_jobs = tuple(deduplicate(jobs))
        potentials = ()
    errors.sort(key=lambda error: (error.provider, error.company.casefold(), error.slug))
    return ScanDetails(
        ScanResult.from_iterables(unique_jobs, errors),
        potentials,
    )
