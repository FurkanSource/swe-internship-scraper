"""Greenhouse public job-board adapter."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

from ..execution import partial_allowed
from ..filters import is_potential_internship_summary
from ..models import Job
from ..normalize import iso_datetime, normalize_locations
from ._reliability import enrich_listing, ordered_details, parse_listing_rows
from .base import JsonClient, Target


def _text(value: Any) -> str:
    clean = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(clean).split())


class GreenhouseProvider:
    name = "greenhouse"
    required_options: tuple[str, ...] = ()

    def validate_target(self, target: Target) -> None:
        if not target.slug.strip():
            raise ValueError("Greenhouse target requires a board slug")
        if not isinstance(target.options.get("listing_only", False), bool):
            raise ValueError("Greenhouse listing_only must be a boolean")

    def fetch(self, target: Target, client: JsonClient) -> list[Job]:
        self.validate_target(target)
        slug = urllib.parse.quote(target.slug, safe="")
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        if not target.options.get("listing_only", False):
            endpoint += "?content=true"
        payload = client.get_json(endpoint)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("Greenhouse response must contain a jobs list")
        if partial_allowed():
            return parse_listing_rows(
                payload["jobs"], lambda row: self.parse(target, {"jobs": [row]})
            )
        jobs = self.parse(target, payload)
        if len(jobs) != len(payload["jobs"]):
            raise ValueError("Greenhouse response contains malformed job records")
        return jobs

    def fetch_candidates(self, target: Target, client: JsonClient) -> list[Job]:
        jobs = self.fetch(target, client)
        return [job for job in jobs if is_potential_internship_summary(job.title)]

    def fetch_candidates_with_details(
        self, target: Target, client: JsonClient
    ) -> list[Job]:
        return enrich_listing(
            lambda: self.fetch(target, client),
            lambda jobs: self._with_details(
                target,
                client,
                [job for job in jobs if is_potential_internship_summary(job.title)],
            ),
        )

    def fetch_with_details(self, target: Target, client: JsonClient) -> list[Job]:
        return enrich_listing(
            lambda: self.fetch(target, client),
            lambda jobs: self._with_details(target, client, jobs),
        )

    def _with_details(
        self, target: Target, client: JsonClient, jobs: list[Job]
    ) -> list[Job]:
        if not target.options.get("listing_only", False):
            return jobs
        slug = urllib.parse.quote(target.slug, safe="")

        def detail(job: Job) -> Job:
            source_id = urllib.parse.quote(job.source_job_id, safe="")
            payload = client.get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{source_id}"
            )
            parsed = self.parse(target, {"jobs": [payload]})
            if len(parsed) != 1 or parsed[0].source_job_id != job.source_job_id:
                raise ValueError("Greenhouse detail response contains a malformed job")
            return parsed[0]

        return ordered_details(detail, jobs, 4)

    def parse(self, target: Target, payload: Any) -> list[Job]:
        """Normalize one recorded or live Greenhouse board response."""
        rows = payload.get("jobs", []) if isinstance(payload, dict) else []
        jobs: list[Job] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("id") or "").strip()
            title = str(row.get("title") or "").strip()
            url = str(row.get("absolute_url") or "").strip()
            if not source_id or not title or not url:
                continue
            location = (row.get("location") or {}).get("name", "")
            jobs.append(
                Job(
                    id=f"greenhouse:{target.slug}:{source_id}",
                    company=target.name,
                    title=title,
                    application_url=url,
                    provider=self.name,
                    source_job_id=source_id,
                    locations=normalize_locations([location]),
                    posted_at=iso_datetime(row.get("updated_at")),
                    description=_text(row.get("content")),
                    remote="remote" in str(location).casefold(),
                    metadata={
                        "source_namespace": f"https://boards-api.greenhouse.io/v1/boards/{target.slug}",
                        "board": target.slug,
                        "source_updated_at_raw": str(row.get("updated_at") or ""),
                    },
                )
            )
        return jobs
