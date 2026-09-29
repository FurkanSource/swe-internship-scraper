"""Cooperative cancellation and optional per-board deadlines across worker threads."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class ScanCancelled(RuntimeError):
    """Stop submitting work and stop providers between requests."""


class BoardDeadlineExceeded(TimeoutError):
    """The caller's total board time budget was exhausted."""


@dataclass(frozen=True)
class FetchControl:
    cancelled: threading.Event
    deadline: float | None = None
    allow_partial: bool = False

    def check(self) -> None:
        if self.cancelled.is_set():
            raise ScanCancelled("scan cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise BoardDeadlineExceeded("board deadline exceeded")

    def remaining(self) -> float | None:
        self.check()
        return None if self.deadline is None else self.deadline - time.monotonic()

    def wait(self, seconds: float) -> None:
        remaining = self.remaining()
        delay = max(0.0, seconds)
        if remaining is not None:
            delay = min(delay, max(0.0, remaining))
        self.cancelled.wait(delay)
        self.check()


_CURRENT: ContextVar[FetchControl | None] = ContextVar("fetch_control", default=None)


@contextmanager
def fetch_context(control: FetchControl) -> Iterator[None]:
    token = _CURRENT.set(control)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_control() -> FetchControl | None:
    return _CURRENT.get()


def check_cancelled() -> None:
    if control := current_control():
        control.check()


def partial_allowed() -> bool:
    control = current_control()
    return control is not None and control.allow_partial


def validate_timeout(value: float | None) -> None:
    if value is not None and (
        isinstance(value, bool) or not math.isfinite(value) or value <= 0
    ):
        raise ValueError("board_timeout must be a finite positive number of seconds")
