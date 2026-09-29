"""Configurable Workday CXS adapter."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from ..filters import is_potential_internship_summary
from ..models import Job
from ..normalize import normalize_locations
from ._reliability import detail_json, ordered_details
from .base import JsonClient, Target


def _site_name(target: Target) -> str:
    return str(target.options.get("site") or target.slug).strip().strip("/")


def _bounded_option(target: Target, name: str, default: int, maximum: int) -> int:
    value = target.options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"Workday {name} must be an integer between 1 and {maximum}")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            f"Workday {name} must be an integer between 1 and {maximum}"
        ) from exc
    if not 1 <= parsed <= maximum:
        raise ValueError(f"Workday {name} must be an integer between 1 and {maximum}")
    return parsed


class WorkdayProvider:
    name = "workday"
    required_options = ("origin", "tenant")

    def validate_target(self, target: Target) -> None:
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = _site_name(target)
        if (
            not origin.startswith("https://")
            or not urlsplit(origin).hostname
            or not tenant
            or not re.fullmatch(r"[\w.-]+", tenant)
            or not re.fullmatch(r"[\w.-]+", site)
            or site in {".", ".."}
        ):
            raise ValueError("Workday target requires HTTPS origin, tenant, and site")
        _bounded_option(target, "page_size", 20, 1000)
        _bounded_option(target, "max_pages", 200, 1000)
        _bounded_option(target, "detail_workers", 4, 8)
        if not isinstance(target.options.get("search_text", ""), str):
            raise ValueError("Workday search_text must be a string")

    def fetch(self, target: Target, client: JsonClient) -> list[Job]:
        self.validate_target(target)
        origin = str(target.options.get("origin") or "").rstrip("/")
        tenant = str(target.options.get("tenant") or "").strip()
        site = _site_name(target)
        endpoint = f"{origin}/wday/cxs/{tenant}/{site}/jobs"
        limit = min(_bounded_option(target, "page_size", 20, 1000), 20)
        max_pages = _bounded_option(target, "max_pages", 200, 1000)
        jobs: list[Job] = []
        expected_total: int | None = None
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for page in range(max_pages):
            payload = {
                "appliedFacets": {},
                "limit": limit,
                "offset": page * limit,
                "searchText": target.options.get("search_text", ""),
            }
            data = client.post_json(
                endpoint,
                payload,
                headers={"Origin": origin, "Referer": f"{origin}/{site}/"},
            )
            # Live CXS pages occasionally contain requisition-only rows that
            # disappear on a fresh request. Recheck that page once, retaining
            # strict validation of every row and the original total below.
            if (
                isinstance(data, dict)
                and isinstance(data.get("jobPostings"), list)
                and type(data.get("total")) is int
                and data["total"] >= 0
                and any(self._row_error(row) for row in data["jobPostings"])
            ):
                original_total = data.get("total")
                data = client.post_json(
                    endpoint,
                    payload,
                    headers={"Origin": origin, "Referer": f"{origin}/{site}/"},
                )
                if isinstance(data, dict) and data.get("total") != original_total:
                    raise RuntimeError(
                        f"Workday board '{target.name}' changed pagination total "
                        f"during row recheck at offset {page * limit}"
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
                index, reason = next(
                    (index, self._row_error(row))
                    for index, row in enumerate(rows)
                    if self._row_error(row)
                )
                raise RuntimeError(
                    f"Workday board '{target.name}' returned invalid pagination rows "
                    f"at offset {page * limit}, row {index}: {reason}"
                )
            for job in page_jobs:
                if job.source_job_id in seen_ids or job.application_url in seen_urls:
                    raise RuntimeError(
                        f"Workday board '{target.name}' repeated pagination rows "
                        f"at offset {page * limit}"
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

    @staticmethod
    def _row_error(row: Any) -> str:
        if not isinstance(row, dict):
            return "row must be an object"
        if not isinstance(row.get("title"), str) or not row["title"].strip():
            return "title must be a non-empty string"
        path = row.get("externalPath")
        if (
            not isinstance(path, str)
            or not path.strip().startswith("/job/")
            or ".." in path.split("/")
            or "?" in path
            or "#" in path
        ):
            return "externalPath must be a direct /job/ path"
        return ""

    def fetch_candidates(self, target: Target, client: JsonClient) -> list[Job]:
        """Return candidate listings without unnecessary description requests."""
        jobs = self.fetch(target, client)
        return [job for job in jobs if is_potential_internship_summary(job.title)]

    def fetch_candidates_with_details(
        self, target: Target, client: JsonClient
    ) -> list[Job]:
        """Enrich candidates when location or keyword constraints need details."""
        return self._details(target, client, self.fetch_candidates(target, client))

    def fetch_with_details(self, target: Target, client: JsonClient) -> list[Job]:
        """Enrich full listings when explicit keyword filters require duties."""
        return self._details(target, client, self.fetch(target, client))

    def _details(
        self, target: Target, client: JsonClient, candidates: list[Job]
    ) -> list[Job]:
        origin = str(target.options["origin"]).rstrip("/")
        tenant = str(target.options["tenant"]).strip()
        site = _site_name(target)

        def fetch_detail(job: Job) -> Job:
            endpoint = f"{origin}/wday/cxs/{tenant}/{site}{job.metadata['external_path']}"
            payload, provenance = detail_json(
                client,
                endpoint,
                job.to_dict(),
                validate=lambda data: (
                    isinstance(data, dict)
                    and isinstance(data.get("jobPostingInfo"), dict)
                    and isinstance(data["jobPostingInfo"].get("jobDescription"), str)
                ),
            )
            info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
            if not isinstance(info, dict) or not isinstance(
                info.get("jobDescription"), str
            ):
                raise RuntimeError(
                    f"Workday posting '{job.source_job_id}' returned malformed details"
                )
            description = re.sub(r"<[^>]+>", " ", info["jobDescription"])
            description = " ".join(html.unescape(description).split())
            locations = (
                normalize_locations(
                    [info.get("location") or "", *(info.get("additionalLocations") or [])]
                )
                or job.locations
            )
            remote_type = str(info.get("remoteType") or "")
            return replace(
                job,
                description=description,
                locations=locations,
                remote=job.remote or remote_type.casefold() == "remote",
                metadata={
                    **job.metadata,
                    "details_complete": True,
                    "remote_type": remote_type,
                    "source_start_date": info.get("startDate", ""),
                    **provenance,
                },
            )

        return ordered_details(
            fetch_detail, candidates, _bounded_option(target, "detail_workers", 4, 8)
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
            if self._row_error(row):
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
                    description="",
                    remote=any("remote" in value.casefold() for value in locations),
                    metadata={
                        "tenant": tenant,
                        "site": site,
                        "posted_on": row.get("postedOn", ""),
                        "external_path": path,
                        "summary_fields": row.get("bulletFields") or [],
                        "details_complete": False,
                        "listing_fingerprint": hashlib.sha256(
                            json.dumps(row, sort_keys=True).encode("utf-8")
                        ).hexdigest(),
                    },
                )
            )
        return jobs
