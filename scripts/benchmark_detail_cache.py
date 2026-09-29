"""Five paired offline replays of 600 current public details.

Run: rtk proxy py -B scripts/benchmark_detail_cache.py
Each simulated request accrues a fixed pacing delay, settled by one sleep within
the measured replay. Batching the delay avoids Windows timer rounding on hundreds
of tiny sleeps. Counters are simulated network requests, not live traffic.
One unpaced disk warmup is reported separately. Changed-row/deletion and TTL
checks run once without pacing to keep the benchmark bounded.
This measures unchanged replay reuse, not full-catalog or live performance.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swe_scraper.detail_cache import DetailCache

RUNS = 5
ROWS = 600
LIST_PACE_SECONDS = 0.002
DETAIL_PACE_SECONDS = 0.010
TTL_SECONDS = 30


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass
class Clock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


class ReplaySource:
    """Current listing fixture plus deterministic per-request pacing debt."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.description_revision = 0
        self.list_calls = 0
        self.detail_calls = 0

    def listings(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [dict(row) for row in self.rows]

    def detail(self, row: dict[str, Any]) -> dict[str, Any]:
        self.detail_calls += 1
        return {
            "id": row["id"],
            "title": row["title"],
            "description": (
                f"Listing revision {row['revision']}; "
                f"description revision {self.description_revision}. " + "x" * 1024
            ),
        }

    def settle_pacing(self) -> None:
        time.sleep(
            self.list_calls * LIST_PACE_SECONDS + self.detail_calls * DETAIL_PACE_SECONDS
        )


@dataclass(frozen=True)
class Measurement:
    seconds: float
    listing_calls: int
    detail_calls: int
    ids: tuple[str, ...]
    payload_sha256: str


def replay(
    source: ReplaySource, cache: DetailCache | None, *, paced: bool = True
) -> Measurement:
    source.list_calls = source.detail_calls = 0
    started = time.perf_counter()
    payloads: list[Any] = []
    for row in source.listings():
        key = {
            "provider": "offline-example",
            "listing_token": digest(row),
            "url": f"https://example.test/jobs/{row['id']}",
            "options": {"locale": "en-US"},
        }
        entry = cache.load(key) if cache is not None else None
        if entry is None:
            payload = source.detail(row)
            entry = cache.save(key, payload) if cache is not None else None
            payloads.append(entry.payload if entry is not None else payload)
        else:
            payloads.append(entry.payload)
    if paced:
        source.settle_pacing()
    elapsed = time.perf_counter() - started
    ids = tuple(str(payload["id"]) for payload in payloads)
    return Measurement(
        elapsed, source.list_calls, source.detail_calls, ids, digest(payloads)
    )


def same_outputs(left: Measurement, right: Measurement) -> None:
    if left.ids != right.ids or left.payload_sha256 != right.payload_sha256:
        raise AssertionError("replay changed IDs or detail payloads")
    if left.listing_calls != 1 or right.listing_calls != 1:
        raise AssertionError("each replay must fetch current listings")


def report(value: Measurement) -> dict[str, object]:
    return {
        "seconds": round(value.seconds, 6),
        "listing_calls": value.listing_calls,
        "detail_calls": value.detail_calls,
        "ids_sha256": digest(value.ids),
        "payload_sha256": value.payload_sha256,
    }


def main() -> None:
    original: list[dict[str, Any]] = [
        {"id": str(index), "title": "Software Intern", "revision": 0, "location": "NYC"}
        for index in range(ROWS)
    ]
    changed = [
        {**row, "title": "Updated Software Intern", "revision": 1}
        if index < 8
        else dict(row)
        for index, row in enumerate(original[:-5])
    ] + [
        {"id": str(index), "title": "Software Intern", "revision": 0, "location": "NYC"}
        for index in range(ROWS, ROWS + 3)
    ]
    baseline_runs: list[Measurement] = []
    warm_runs: list[Measurement] = []
    with tempfile.TemporaryDirectory(prefix="swe-detail-replay-") as directory:
        root = Path(directory) / "details"
        clock = Clock(datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
        cache = DetailCache(root, ttl_seconds=TTL_SECONDS, now=clock.now)
        cold = replay(ReplaySource(original), cache, paced=False)
        print(json.dumps({"unpaced_disk_warmup": report(cold)}, sort_keys=True), flush=True)
        for run in range(RUNS):
            # New objects prove disk reuse; the injected clock stays inside TTL.
            cache = DetailCache(root, ttl_seconds=TTL_SECONDS, now=clock.now)
            clock.instant += timedelta(seconds=1)
            if run % 2:
                warm = replay(ReplaySource(original), cache)
                baseline = replay(ReplaySource(original), None)
            else:
                baseline = replay(ReplaySource(original), None)
                warm = replay(ReplaySource(original), cache)
            same_outputs(baseline, cold)
            same_outputs(baseline, warm)
            if baseline.detail_calls != ROWS or warm.detail_calls != 0:
                raise AssertionError("unchanged replay did not retain all 600 details")
            baseline_runs.append(baseline)
            warm_runs.append(warm)
            print(
                json.dumps(
                    {
                        "run": run + 1,
                        "uncached": report(baseline),
                        "unchanged_cache": report(warm),
                        "identical_ids_and_payloads": True,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        # Correctness checks are intentionally unpaced and excluded from medians.
        changed_baseline = replay(ReplaySource(changed), None, paced=False)
        changed_cached = replay(ReplaySource(changed), cache, paced=False)
        same_outputs(changed_baseline, changed_cached)
        if changed_cached.detail_calls != 11:
            raise AssertionError("8 changed and 3 new rows must fetch 11 details")
        removed = {str(index) for index in range(ROWS - 5, ROWS)}
        if removed.intersection(changed_cached.ids):
            raise AssertionError("cached deleted listings reappeared")

        # One current detail is enough to demonstrate description-only lag/expiry.
        description_source = ReplaySource(changed[:1])
        description_source.description_revision = 1
        old = replay(ReplaySource(changed[:1]), cache, paced=False)
        lagged = replay(description_source, cache, paced=False)
        same_outputs(old, lagged)
        if lagged.detail_calls != 0:
            raise AssertionError("description-only replay must hit before TTL")
        clock.instant += timedelta(seconds=TTL_SECONDS)
        expired = replay(description_source, cache, paced=False)
        fresh = replay(description_source, None, paced=False)
        same_outputs(fresh, expired)
        if expired.detail_calls != 1 or expired.payload_sha256 == lagged.payload_sha256:
            raise AssertionError("description update must appear after TTL expiry")

        uncached_median = statistics.median(row.seconds for row in baseline_runs)
        cached_median = statistics.median(row.seconds for row in warm_runs)
        uncached_calls = sum(row.detail_calls for row in baseline_runs)
        cached_calls = sum(row.detail_calls for row in warm_runs)
        calls_reduction = 1 - cached_calls / uncached_calls
        time_reduction = 1 - cached_median / uncached_median
        passed = calls_reduction >= 0.5 and time_reduction >= 0.2
        print(
            json.dumps(
                {
                    "summary": "offline paced unchanged replay; no full-catalog/live claim",
                    "runs": RUNS,
                    "rows": ROWS,
                    "list_pace_seconds": LIST_PACE_SECONDS,
                    "detail_pace_seconds": DETAIL_PACE_SECONDS,
                    "pacing": "per-request delay settled once within measured replay",
                    "uncached_detail_calls": uncached_calls,
                    "cached_detail_calls": cached_calls,
                    "detail_call_reduction_percent": round(100 * calls_reduction, 2),
                    "uncached_median_seconds": round(uncached_median, 6),
                    "cached_median_seconds": round(cached_median, 6),
                    "median_time_reduction_percent": round(100 * time_reduction, 2),
                    "unpaced_disk_warmup_seconds": round(cold.seconds, 6),
                    "cache_files_after_warmup": ROWS,
                    "retained_warm_hits_per_run": [
                        ROWS - row.detail_calls for row in warm_runs
                    ],
                    "changed_detail_calls": changed_cached.detail_calls,
                    "changed_rows": 8,
                    "new_rows": 3,
                    "deleted_ids": sorted(removed),
                    "deleted_ids_absent": True,
                    "changed_identical_ids_and_payloads": True,
                    "current_rows_after_change": len(changed),
                    "listing_calls_per_replay": 1,
                    "description_only_changes_may_lag_until_ttl": True,
                    "ttl_refetched_description_probe_calls": expired.detail_calls,
                    "acceptance_passed": passed,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if not passed:
            raise SystemExit("unchanged replay missed the 50% calls / 20% time targets")


if __name__ == "__main__":
    main()
