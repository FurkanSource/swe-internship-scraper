"""Configurable Workday CXS adapter."""

from __future__ import annotations

import re
from typing import Any

from ..models import Job
from ..normalize import normalize_locations
from .base import JsonClient, Target


def _site_name(target: Target) -> str:
    return str(target.options.get("site") or target.slug).strip().strip("/")


class WorkdayProvider:
    name = "workday"
    required_options = ("origin", "tenant")

    def validate_target(self, target: Target) -> None:
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = _site_name(target)
        if (
            not origin.startswith("https://")
            or not tenant
            or not re.fullmatch(r"[\w.-]+", site)
            or site in {".", ".."}
        ):
            raise ValueError("Workday target requires HTTPS origin, tenant, and site")

    def fetch(self, target: Target, client: JsonClient) -> list[Job]:
        self.validate_target(target)
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = _site_name(target)
        endpoint = f"{origin}/wday/cxs/{tenant}/{site}/jobs"
        limit = min(max(int(target.options.get("page_size", 20)), 1), 20)
        max_pages = min(max(int(target.options.get("max_pages", 20)), 1), 20)
        jobs: list[Job] = []
        expected_total: int | None = None
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for page in range(max_pages):
            payload = {
                "appliedFacets": {},
                "limit": limit,
                "offset": page * limit,
                "searchText": str(target.options.get("search_text", "intern")),
            }
            data = client.post_json(
                endpoint,
                payload,
                headers={"Origin": origin, "Referer": f"{origin}/{site}/"},
            )
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"Workday board '{target.name}' returned non-dict response: "
                    f"{type(data).__name__}"
                )
            rows = data.get("jobPostings")
            total = data.get("total")
            if (
                not isinstance(rows, list)
                or not isinstance(total, int)
                or isinstance(total, bool)
                or total < 0
            ):
                raise RuntimeError(
                    f"Workday board '{target.name}' returned invalid pagination data"
                )
            # CXS can report total=0 on continuation pages. The first page's
            # total remains the completeness contract; zero must not end a scan.
            if expected_total is None:
                expected_total = total
            elif total not in (0, expected_total):
                raise RuntimeError(
                    f"Workday board '{target.name}' changed pagination total"
                )
            page_jobs = self.parse_page(target, data)
            if len(page_jobs) != len(rows):
                raise RuntimeError(
                    f"Workday board '{target.name}' returned invalid pagination rows"
                )
            for job in page_jobs:
                if job.source_job_id in seen_ids or job.application_url in seen_urls:
                    raise RuntimeError(
                        f"Workday board '{target.name}' repeated pagination rows"
                    )
                seen_ids.add(job.source_job_id)
                seen_urls.add(job.application_url)
            jobs.extend(page_jobs)
            if len(rows) > limit or len(jobs) > expected_total:
                raise RuntimeError(
                    f"Workday board '{target.name}' returned inconsistent pagination counts"
                )
            if len(jobs) == expected_total:
                return jobs
            if len(rows) < limit:
                raise RuntimeError(
                    f"Workday board '{target.name}' returned incomplete pagination "
                    f"({len(jobs)} of {expected_total} jobs)"
                )
        raise RuntimeError(
            f"Workday board '{target.name}' exceeded configured pagination "
            f"limit ({max_pages} pages)"
        )

    def parse_page(self, target: Target, payload: Any) -> list[Job]:
        """Normalize one recorded or live Workday CXS result page."""
        if not isinstance(payload, dict):
            return []
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = _site_name(target)
        jobs: list[Job] = []
        for row in payload.get("jobPostings") or []:
            if not isinstance(row, dict):
                continue
            path = str(row.get("externalPath") or "").strip()
            title = str(row.get("title") or "").strip()
            if not path.startswith("/job/") or not title:
                continue
            source_id = str(row.get("jobReqId") or path).strip()
            locations = normalize_locations([row.get("locationsText") or ""])
            jobs.append(
                Job(
                    id=f"workday:{tenant}:{source_id}",
                    company=target.name,
                    title=title,
                    application_url=f"{origin}/{site}{path}",
                    provider=self.name,
                    source_job_id=source_id,
                    locations=locations,
                    description=" ".join(
                        str(value) for value in (row.get("bulletFields") or []) if value
                    ),
                    remote=any("remote" in value.casefold() for value in locations),
                    metadata={
                        "tenant": tenant,
                        "site": site,
                        "posted_on": row.get("postedOn", ""),
                        "external_path": path,
                    },
                )
            )
        return jobs
