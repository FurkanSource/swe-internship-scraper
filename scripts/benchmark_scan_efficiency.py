"""Offline list/detail replay for comparing full and candidate fetch paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swe_scraper.config import load_targets
from swe_scraper.filters import filter_jobs
from swe_scraper.providers.base import Target
from swe_scraper.providers.http import RequestsJsonClient
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider

COUNT = 1_000


def title_for(index: int) -> str:
    if index % 20 == 0:
        return "Software Engineering Intern"
    if index % 33 == 0:
        return "Student Program"
    return "Senior Software Engineer"


class ReplayClient:
    def __init__(self, provider: str):
        self.provider = provider
        self.list_calls = 0
        self.detail_calls = 0
        self.lock = threading.Lock()

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        if self.provider == "smartrecruiters":
            is_list = url.endswith("/postings")
            if is_list:
                offset = int(kwargs["params"]["offset"])
                limit = int(kwargs["params"]["limit"])
            else:
                source_id = urllib.parse.unquote(url.rsplit("/", 1)[1])
        else:
            is_list = "recruitingCEJobRequisitions?" in url
            finder = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["finder"][0]
            if is_list:
                offset = int(finder.rsplit("offset=", 1)[1])
                limit = int(finder.split("limit=", 1)[1].split(",", 1)[0])
            else:
                source_id = finder.split('Id="', 1)[1].split('"', 1)[0]
        if is_list:
            with self.lock:
                self.list_calls += 1
            ids = range(offset, min(offset + limit, COUNT))
            if self.provider == "smartrecruiters":
                return {
                    "totalFound": COUNT,
                    "content": [
                        {"id": str(index), "name": title_for(index)} for index in ids
                    ],
                }
            return {
                "items": [
                    {
                        "TotalJobsCount": COUNT,
                        "requisitionList": {
                            "items": [
                                {"Id": str(index), "Title": title_for(index)}
                                for index in ids
                            ],
                            "hasMore": offset + limit < COUNT,
                        },
                    }
                ]
            }
        with self.lock:
            self.detail_calls += 1
        time.sleep(0.001)
        index = int(source_id)
        detail_title = (
            "Software Intern" if title_for(index) == "Student Program" else title_for(index)
        )
        if self.provider == "smartrecruiters":
            return {"id": source_id, "name": detail_title}
        return {"items": [{"Id": source_id, "Title": detail_title}]}


def replay(provider: Any, target: Target, *, candidates_only: bool) -> dict[str, Any]:
    client = ReplayClient(provider.name)
    started = time.perf_counter()
    jobs = (
        provider.fetch_candidates(target, client)
        if candidates_only
        else provider.fetch(target, client)
    )
    elapsed = time.perf_counter() - started
    kept = sorted((job.source_job_id, job.application_url) for job in filter_jobs(jobs))
    digest = hashlib.sha256(json.dumps(kept).encode("utf-8")).hexdigest()
    return {
        "seconds": elapsed,
        "list_requests": client.list_calls,
        "detail_requests": client.detail_calls,
        "matching_jobs": len(kept),
        "output_sha256": digest,
    }


class CountingClient:
    """Count public list and detail requests while retaining normal HTTP pacing."""

    def __init__(self, provider: str):
        self.provider = provider
        self.http = RequestsJsonClient()
        self.lock = threading.Lock()
        self.list_calls = 0
        self.detail_calls = 0

    def get_json(self, url: str, **kwargs: Any) -> Any:
        is_list = (
            url.endswith("/postings")
            if self.provider == "smartrecruiters"
            else "recruitingCEJobRequisitions?" in url
        )
        with self.lock:
            if is_list:
                self.list_calls += 1
            else:
                self.detail_calls += 1
        return self.http.get_json(url, **kwargs)


def live_replay(provider: Any, target: Target, *, candidates_only: bool) -> dict[str, Any]:
    client = CountingClient(provider.name)
    started = time.perf_counter()
    jobs = (
        provider.fetch_candidates(target, client)
        if candidates_only
        else provider.fetch(target, client)
    )
    kept = sorted((job.source_job_id, job.application_url) for job in filter_jobs(jobs))
    return {
        "seconds": round(time.perf_counter() - started, 3),
        "list_requests": client.list_calls,
        "detail_requests": client.detail_calls,
        "matching_jobs": len(kept),
        "output_sha256": hashlib.sha256(json.dumps(kept).encode("utf-8")).hexdigest(),
    }


def live_sample(max_postings: int) -> None:
    canaries = load_targets(profile="canary")
    for provider, name in (
        (SmartRecruitersProvider(), "Apex Clean Energy"),
        (OracleProvider(), "Duracell"),
    ):
        target = next(
            value
            for value in canaries
            if value.provider == provider.name and value.name == name
        )
        preflight = RequestsJsonClient()
        if provider.name == "smartrecruiters":
            endpoint = (
                "https://api.smartrecruiters.com/v1/companies/"
                f"{urllib.parse.quote(target.slug, safe='')}/postings"
            )
            count = len(provider._fetch_posting_ids(target, preflight, endpoint))
        else:
            count = len(provider._fetch_summaries(target, preflight))
        if count > max_postings:
            raise RuntimeError(f"{name} has {count} postings; live cap is {max_postings}")
        full = live_replay(provider, target, candidates_only=False)
        fast = live_replay(provider, target, candidates_only=True)
        if full["output_sha256"] != fast["output_sha256"]:
            raise AssertionError(f"{name} lost a qualifying job or changed a direct URL")
        print(
            json.dumps(
                {
                    "provider": provider.name,
                    "target": name,
                    "postings": count,
                    "full": full,
                    "candidate": fast,
                },
                sort_keys=True,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="sample two public canaries")
    parser.add_argument("--max-postings", type=int, default=50)
    args = parser.parse_args()
    if args.live:
        live_sample(args.max_postings)
        return
    for provider, target in (
        (
            SmartRecruitersProvider(),
            Target("smartrecruiters", "Example", "example", {"detail_workers": 4}),
        ),
        (
            OracleProvider(),
            Target(
                "oracle",
                "Example",
                "example",
                {"origin": "https://example.test", "page_size": 100, "detail_workers": 4},
            ),
        ),
    ):
        full = [replay(provider, target, candidates_only=False) for _ in range(3)]
        fast = [replay(provider, target, candidates_only=True) for _ in range(3)]
        if any(row["output_sha256"] != full[0]["output_sha256"] for row in fast):
            raise AssertionError("candidate fetch changed matching jobs or direct URLs")
        result = {
            "provider": provider.name,
            "list_requests": full[0]["list_requests"],
            "full_detail_requests": full[0]["detail_requests"],
            "candidate_detail_requests": fast[0]["detail_requests"],
            "matching_jobs": full[0]["matching_jobs"],
            "full_median_seconds": round(
                statistics.median(row["seconds"] for row in full), 3
            ),
            "candidate_median_seconds": round(
                statistics.median(row["seconds"] for row in fast), 3
            ),
            "output_sha256": full[0]["output_sha256"],
        }
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
