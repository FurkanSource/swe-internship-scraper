"""Bounded detail fetching and retries for changing pagination totals."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from .base import Target

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


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
    fetch: Callable[[_Item], _Result], items: Iterable[_Item], workers: int
) -> list[_Result]:
    """Preserve listing order and propagate failures without returning partial data.

    Callers retain their shared HTTP client, including its host rate limiter.
    One worker runs inline for custom clients that require serial access.
    """
    if workers == 1:
        return [fetch(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(fetch, items))
