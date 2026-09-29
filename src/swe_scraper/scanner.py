"""Concurrent, failure-isolated provider orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from threading import Event
from time import monotonic
from typing import cast

from .checkpoints import ScanCheckpointStore
from .dedupe import PotentialDuplicate, deduplicate, deduplicate_with_audit
from .execution import FetchControl, ScanCancelled, fetch_context, validate_timeout
from .filters import filter_jobs
from .models import Job, ProviderFailure, ScanResult
from .providers import Target, get_provider
from .providers.base import HttpClient, Provider
from .providers.http import RequestsJsonClient
from .providers.results import PartialFetchError


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
    allow_partial: bool = False,
    board_timeout: float | None = None,
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
        allow_partial=allow_partial,
        board_timeout=board_timeout,
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
    allow_partial: bool = False,
    board_timeout: float | None = None,
) -> ScanDetails:
    """Fetch targets and retain uncertain duplicate pairs for optional auditing."""
    validate_timeout(board_timeout)
    target_list = list(targets)
    include_terms = tuple(include_keywords)
    exclude_terms = tuple(exclude_keywords)
    location_terms = tuple(locations)
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
            enriched_fetch = (
                getattr(provider, "fetch_candidates_with_details", None)
                if callable(getattr(type(provider), "fetch_candidates_with_details", None))
                else None
            )
            if (include_terms or exclude_terms or location_terms) and callable(
                enriched_fetch
            ):
                return cast(list[Job], enriched_fetch(target, http))
            return cast(list[Job], candidate_fetch(target, http))
        detail_fetch = (
            getattr(provider, "fetch_with_details", None)
            if callable(getattr(type(provider), "fetch_with_details", None))
            else None
        )
        if (include_terms or exclude_terms or location_terms) and callable(detail_fetch):
            return cast(list[Job], detail_fetch(target, http))
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
    cancelled = Event()

    def controlled_fetch(target: Target) -> list[Job]:
        deadline = None if board_timeout is None else monotonic() + board_timeout
        control = FetchControl(cancelled, deadline, allow_partial)
        with fetch_context(control):
            control.check()
            result = fetch_one(target)
            control.check()
            return result

    pool = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Future[list[Job]], tuple[int, Target]] = {}
    remaining = iter(pending_targets)

    def replenish() -> None:
        while len(futures) < workers:
            item = next(remaining, None)
            if item is None:
                break
            futures[pool.submit(controlled_fetch, item[1])] = item

    def consume(future: Future[list[Job]]) -> None:
        nonlocal completed
        index, target = futures.pop(future)
        try:
            fetched = future.result()
        except PartialFetchError as exc:
            partial_jobs = exc.result.jobs if allow_partial else ()
            jobs.extend(
                replace(job, metadata={**job.metadata, "board_complete": False})
                for job in partial_jobs
            )
            errors.append(
                ProviderFailure(
                    target.provider,
                    target.name,
                    target.slug,
                    str(exc),
                    partial=bool(partial_jobs),
                    details=tuple(issue.to_dict() for issue in exc.result.issues),
                )
            )
        except Exception as exc:
            errors.append(
                ProviderFailure(
                    target.provider,
                    target.name,
                    target.slug,
                    f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            if checkpoint_store:
                checkpoint_store.save(index, target, fetched)
            jobs.extend(fetched)
        completed += 1

    try:
        replenish()
        while futures:
            done, _ = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda value: futures[value][0]):
                consume(future)
                report_progress()
            if not done:
                report_progress()
            replenish()
    except BaseException:
        cancelled.set()
        for future in futures:
            future.cancel()
        # Cooperative providers leave after their active request. Preserve any
        # complete boards that finish while stopping, without reentering callbacks.
        pool.shutdown(wait=True, cancel_futures=True)
        for future in list(futures):
            if future.cancelled():
                continue
            try:
                if not isinstance(future.exception(), ScanCancelled):
                    consume(future)
            except Exception:
                # Cleanup must not mask the original interruption/export error.
                pass
        raise
    else:
        pool.shutdown(wait=True)

    jobs = filter_jobs(
        jobs,
        filter_swe=filter_swe,
        include_adjacent=include_adjacent,
        locations=location_terms,
        include_keywords=include_terms,
        exclude_keywords=exclude_terms,
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
