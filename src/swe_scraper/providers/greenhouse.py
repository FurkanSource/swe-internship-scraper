"""Greenhouse public job-board adapter."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

from ..models import Job
from ..normalize import iso_datetime, normalize_locations
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

    def fetch(self, target: Target, client: JsonClient) -> list[Job]:
        self.validate_target(target)
        slug = urllib.parse.quote(target.slug, safe="")
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        payload = client.get_json(endpoint)
        return self.parse(target, payload)

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
                        "board": target.slug,
                        "source_updated_at_raw": str(row.get("updated_at") or ""),
                    },
                )
            )
        return jobs
