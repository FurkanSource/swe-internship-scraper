"""Configurable Workday CXS adapter."""

from __future__ import annotations

from typing import Any

from ..models import Job
from ..normalize import normalize_locations
from .base import JsonClient, Target


class WorkdayProvider:
    name = "workday"
    required_options = ("origin", "tenant")

    def validate_target(self, target: Target) -> None:
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = str(target.options.get("site") or target.slug).strip()
        if not origin.startswith("https://") or not tenant or not site:
            raise ValueError("Workday target requires HTTPS origin, tenant, and site")

    def fetch(self, target: Target, client: JsonClient) -> list[Job]:
        self.validate_target(target)
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = str(target.options.get("site") or target.slug).strip()
        endpoint = f"{origin}/wday/cxs/{tenant}/{site}/jobs"
        limit = min(max(int(target.options.get("page_size", 20)), 1), 100)
        max_pages = min(max(int(target.options.get("max_pages", 5)), 1), 20)
        jobs: list[Job] = []
        last_total: int | None = None
        last_page_size = 0
        for page in range(max_pages):
            payload = {
                "appliedFacets": {},
                "limit": limit,
                "offset": page * limit,
                "searchText": str(target.options.get("search_text") or "intern"),
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
            rows = data.get("jobPostings") or []
            last_page_size = len(rows)
            jobs.extend(self.parse_page(target, data))
            total = data.get("total")
            last_total = total if isinstance(total, int) else None
            complete = isinstance(total, int) and (page + 1) * limit >= total
            if len(rows) < limit or complete:
                break
        else:
            if (last_total is not None and last_total > max_pages * limit) or (
                last_total is None and last_page_size == limit
            ):
                raise RuntimeError(
                    f"Workday board '{target.name}' exceeded configured pagination "
                    f"limit ({max_pages} pages)"
                )
        return jobs

    def parse_page(self, target: Target, payload: Any) -> list[Job]:
        """Normalize one recorded or live Workday CXS result page."""
        if not isinstance(payload, dict):
            return []
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = str(target.options.get("site") or target.slug).strip()
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
