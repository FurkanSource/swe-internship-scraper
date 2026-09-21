"""Per-target health checks and provider quorum evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter

from .models import ScanResult
from .providers import Target, get_provider
from .providers.base import HttpClient
from .providers.http import RequestsJsonClient


class HealthStatus(str, Enum):
    """Health state ordered from successful to release blocking."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class TargetHealth:
    """Result of checking one configured provider target."""

    provider: str
    target: str
    status: HealthStatus
    job_count: int
    latency_seconds: float
    error: str = ""
    category: str = ""
    pagination_complete: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "target": self.target,
            "status": self.status.value,
            "job_count": self.job_count,
            "latency_seconds": round(self.latency_seconds, 6),
            "error": self.error,
            "category": self.category,
            "pagination_complete": self.pagination_complete,
        }


@dataclass(frozen=True, slots=True)
class TargetHealthReport:
    """Health summary for all checked targets and providers."""

    status: HealthStatus
    checks: tuple[TargetHealth, ...]
    provider_statuses: dict[str, HealthStatus]
    quorum: int
    checked_at: str

    @property
    def exit_code(self) -> int:
        return {
            HealthStatus.HEALTHY: 0,
            HealthStatus.DEGRADED: 1,
            HealthStatus.UNHEALTHY: 2,
        }[self.status]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "exit_code": self.exit_code,
            "quorum": self.quorum,
            "checked_at": self.checked_at,
            "providers": {
                name: status.value
                for name, status in sorted(self.provider_statuses.items())
            },
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Legacy aggregate health report retained for API compatibility."""

    healthy: bool
    expected_providers: tuple[str, ...]
    observed_providers: tuple[str, ...]
    errors: tuple[dict[str, str], ...]
    provider_counts: dict[str, int]
    missing_providers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "expected_providers": list(self.expected_providers),
            "observed_providers": list(self.observed_providers),
            "errors": list(self.errors),
            "provider_counts": dict(self.provider_counts),
            "missing_providers": list(self.missing_providers),
        }


def evaluate_health(result: ScanResult, expected_providers: Iterable[str]) -> HealthReport:
    """Evaluate the pre-1.0 aggregate provider health contract."""
    expected = tuple(sorted({value.casefold() for value in expected_providers}))
    observed = tuple(sorted({job.provider.casefold() for job in result.jobs}))
    errors = tuple(error.to_dict() for error in result.errors)
    provider_counts = {
        provider: sum(job.provider.casefold() == provider for job in result.jobs)
        for provider in observed
    }
    missing = tuple(sorted(set(expected) - set(observed)))
    return HealthReport(
        healthy=not missing and not errors,
        expected_providers=expected,
        observed_providers=observed,
        errors=errors,
        provider_counts=provider_counts,
        missing_providers=missing,
    )


def evaluate_target_health(
    checks: Iterable[TargetHealth], *, quorum: int = 2
) -> TargetHealthReport:
    """Apply provider quorum and contract failure rules to target checks."""
    if quorum < 1:
        raise ValueError("health quorum must be at least 1")
    ordered = tuple(sorted(checks, key=lambda row: (row.provider, row.target.casefold())))
    grouped: dict[str, list[TargetHealth]] = defaultdict(list)
    for check in ordered:
        grouped[check.provider.casefold()].append(check)

    provider_statuses: dict[str, HealthStatus] = {}
    for provider, provider_checks in grouped.items():
        healthy_count = sum(
            check.status is HealthStatus.HEALTHY for check in provider_checks
        )
        has_contract_failure = any(
            check.category in {"contract", "pagination"} or not check.pagination_complete
            for check in provider_checks
        )
        if has_contract_failure or healthy_count < quorum:
            provider_statuses[provider] = HealthStatus.UNHEALTHY
        elif healthy_count == len(provider_checks):
            provider_statuses[provider] = HealthStatus.HEALTHY
        else:
            provider_statuses[provider] = HealthStatus.DEGRADED

    states = set(provider_statuses.values())
    if HealthStatus.UNHEALTHY in states or not provider_statuses:
        status = HealthStatus.UNHEALTHY
    elif HealthStatus.DEGRADED in states:
        status = HealthStatus.DEGRADED
    else:
        status = HealthStatus.HEALTHY

    return TargetHealthReport(
        status=status,
        checks=ordered,
        provider_statuses=provider_statuses,
        quorum=quorum,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


def _failure_category(exc: Exception) -> str:
    message = str(exc).casefold()
    if "pagination" in message or "hasmore" in message:
        return "pagination"
    if "timeout" in message:
        return "timeout"
    if "http" in message or "status" in message:
        return "http"
    if isinstance(exc, ValueError):
        return "contract"
    return "provider"


def run_health_checks(
    targets: Iterable[Target],
    *,
    client: HttpClient | None = None,
    max_workers: int = 8,
    quorum: int = 2,
) -> TargetHealthReport:
    """Fetch each canary independently and return categorized target results."""
    target_list = list(targets)
    http = client or RequestsJsonClient()

    def check_one(target: Target) -> TargetHealth:
        started = perf_counter()
        try:
            provider = get_provider(target.provider)
            provider.validate_target(target)
            jobs = provider.fetch(target, http)
            elapsed = perf_counter() - started
            if not jobs:
                return TargetHealth(
                    target.provider,
                    target.name,
                    HealthStatus.UNHEALTHY,
                    0,
                    elapsed,
                    "provider returned no jobs",
                    "empty",
                )
            return TargetHealth(
                target.provider,
                target.name,
                HealthStatus.HEALTHY,
                len(jobs),
                elapsed,
            )
        except Exception as exc:
            category = _failure_category(exc)
            return TargetHealth(
                target.provider,
                target.name,
                HealthStatus.UNHEALTHY,
                0,
                perf_counter() - started,
                f"{type(exc).__name__}: {exc}",
                category,
                category != "pagination",
            )

    workers = min(max(1, int(max_workers)), max(1, len(target_list)), 32)
    checks: list[TargetHealth] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(check_one, target) for target in target_list]
        for future in as_completed(futures):
            checks.append(future.result())
    return evaluate_target_health(checks, quorum=quorum)
