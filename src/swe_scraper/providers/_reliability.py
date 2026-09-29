"""Bounded detail fetching and retries for changing pagination totals."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from typing import Any, TypeVar

from ..execution import (
    BoardDeadlineExceeded,
    ScanCancelled,
    check_cancelled,
    partial_allowed,
)
from ..models import Job
from .base import HttpClient, Target
from .results import BoardFetchResult, FetchIssue, PartialFetchError

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def detail_json(
    client: HttpClient,
    url: str,
    listing: Any,
    *,
    validate: Callable[[Any], bool] | None = None,
    **kwargs: Any,
) -> tuple[Any, dict[str, Any]]:
    """Use optional cache-aware transport without changing custom client contracts."""
    check_cancelled()
    if callable(getattr(type(client), "get_detail_json", None)):
        payload, provenance = client.get_detail_json(  # type: ignore[attr-defined]
            url, listing, validate=validate, **kwargs
        )
        return payload, provenance
    return client.get_json(url, **kwargs), {}


class PaginationTotalChanged(RuntimeError):
    """A valid reported total changed within one pagination attempt."""


def restart_on_total_change(fetch: Callable[[], _Result]) -> _Result:
    """Allow one clean listing restart, propagating every other failure."""
    try:
        return fetch()
    except PaginationTotalChanged:
        return fetch()


def detail_workers(target: Target) -> int:
    value = target.options.get("detail_workers", 4)
    message = "detail_workers must be an integer between 1 and 8"
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(message)
    try:
        workers = int(value)
    except ValueError as exc:
        raise ValueError(message) from exc
    if not 1 <= workers <= 8:
        raise ValueError(message)
    return workers


def ordered_details(
    fetch: Callable[[_Item], Job],
    items: Iterable[_Item],
    workers: int,
    *,
    listing_issues: tuple[FetchIssue, ...] = (),
) -> list[Job]:
    """Preserve listing order, with explicit recovery only under partial mode.

    Callers retain their shared HTTP client, including its host rate limiter.
    One worker runs inline for custom clients that require serial access.
    """
    jobs: list[Job] = []
    issues: list[FetchIssue] = list(listing_issues)

    def one(item: _Item) -> Job | FetchIssue:
        check_cancelled()
        try:
            return fetch(item)
        except (ScanCancelled, BoardDeadlineExceeded):
            raise
        except Exception as exc:
            if not partial_allowed():
                raise
            if isinstance(item, dict):
                source_id = str(
                    item.get("id") or item.get("Id") or item.get("RequisitionNumber") or ""
                )
            else:
                source_id = str(getattr(item, "source_job_id", ""))
            return FetchIssue("detail", source_id, f"{type(exc).__name__}: {exc}")

    def collect(value: Job | FetchIssue) -> None:
        if isinstance(value, FetchIssue):
            issues.append(value)
        else:
            jobs.append(value)

    if workers == 1:
        for item in items:
            collect(one(item))
    else:
        # Bound in-flight work without waiting for the slowest item in a batch.
        # Context follows every worker; output order remains listing order.
        iterator = iter(enumerate(items))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            active: dict[Future[Job | FetchIssue], int] = {}
            results: dict[int, Job | FetchIssue] = {}

            def replenish() -> None:
                check_cancelled()
                while len(active) < workers:
                    try:
                        index, item = next(iterator)
                    except StopIteration:
                        break
                    active[executor.submit(copy_context().run, one, item)] = index

            try:
                replenish()
                while active:
                    done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                    check_cancelled()
                    for future in done:
                        results[active.pop(future)] = future.result()
                    replenish()
            except BaseException:
                for future in active:
                    future.cancel()
                raise
            for index in sorted(results):
                collect(results[index])
    if issues:
        raise PartialFetchError(
            BoardFetchResult(tuple(jobs), tuple(issues), not listing_issues)
        )
    return jobs


def enrich_listing(
    fetch: Callable[[], list[Job]], enrich: Callable[[list[Job]], list[Job]]
) -> list[Job]:
    """Enrich recovered listings too, retaining listing and detail failures."""
    listing: BoardFetchResult | None = None
    try:
        jobs = fetch()
    except PartialFetchError as exc:
        if not partial_allowed():
            raise
        listing = exc.result
        jobs = list(listing.jobs)
    try:
        jobs = enrich(jobs)
    except PartialFetchError as exc:
        if listing is None:
            raise
        raise PartialFetchError(
            BoardFetchResult(
                exc.result.jobs,
                (*listing.issues, *exc.result.issues),
                listing.pagination_complete and exc.result.pagination_complete,
            )
        ) from exc
    if listing is not None:
        raise PartialFetchError(
            BoardFetchResult(tuple(jobs), listing.issues, listing.pagination_complete)
        )
    return jobs


def listing_snapshot(
    fetch: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], tuple[FetchIssue, ...]]:
    """Restart drift once, then retain only the final attempt's verified prefix."""
    retained: list[dict[str, Any]] = []

    def attempt() -> list[dict[str, Any]]:
        retained.clear()
        return fetch(retained)

    try:
        return restart_on_total_change(attempt), ()
    except (ScanCancelled, BoardDeadlineExceeded):
        raise
    except Exception as exc:
        if not partial_allowed():
            raise
        return retained, (FetchIssue("pagination", "", f"{type(exc).__name__}: {exc}"),)


def recover_listing(fetch: Callable[[], list[Job]], jobs: list[Job]) -> list[Job]:
    """Recover validated rows from a broken listing only under explicit opt-in."""
    try:
        return fetch()
    except (ScanCancelled, BoardDeadlineExceeded, PartialFetchError):
        raise
    except Exception as exc:
        if not partial_allowed():
            raise
        raise PartialFetchError(
            BoardFetchResult(
                tuple(jobs),
                (FetchIssue("pagination", "", f"{type(exc).__name__}: {exc}"),),
                False,
            )
        ) from exc


def parse_listing_rows(rows: list[Any], parse: Callable[[Any], list[Job]]) -> list[Job]:
    """Opt-in row isolation after a complete single-response list was validated."""
    jobs: list[Job] = []
    issues: list[FetchIssue] = []
    for index, row in enumerate(rows):
        check_cancelled()
        try:
            parsed = parse(row)
            if len(parsed) != 1:
                raise ValueError("malformed job record")
            jobs.extend(parsed)
        except (ScanCancelled, BoardDeadlineExceeded):
            raise
        except Exception as exc:
            source_id = (
                str(row.get("id") or row.get("jobPostingId") or index)
                if isinstance(row, dict)
                else str(index)
            )
            issues.append(FetchIssue("record", source_id, f"{type(exc).__name__}: {exc}"))
    if issues:
        raise PartialFetchError(BoardFetchResult(tuple(jobs), tuple(issues), True))
    return jobs
