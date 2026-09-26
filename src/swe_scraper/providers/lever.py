"""Lever public postings adapter."""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..models import Job
from ..normalize import iso_datetime, normalize_locations
from .base import JsonClient, Target


class LeverProvider:
    name = "lever"
    required_options: tuple[str, ...] = ()

    def validate_target(self, target: Target) -> None:
        if not target.slug.strip():
            raise ValueError("Lever target requires a site slug")

    def fetch(self, target: Target, client: JsonClient) -> list[Job]:
        self.validate_target(target)
        slug = urllib.parse.quote(target.slug, safe="")
        payload = client.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if not isinstance(payload, list):
            raise ValueError("Lever response must be a postings list")
        jobs = self.parse(target, payload)
        if len(jobs) != len(payload):
            raise ValueError("Lever response contains malformed job records")
        return jobs

    def parse(self, target: Target, payload: Any) -> list[Job]:
        """Normalize one recorded or live Lever board response."""
        rows = payload
        if not isinstance(rows, list):
            return []
        jobs: list[Job] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("id") or "").strip()
            title = str(row.get("text") or "").strip()
            url = str(row.get("applyUrl") or row.get("hostedUrl") or "").strip()
            if not source_id or not title or not url:
                continue
            categories = row.get("categories") or {}
            locations = categories.get("allLocations") or [categories.get("location", "")]
            description_parts = [str(row.get("descriptionPlain") or "")]
            for section in row.get("lists") or []:
                if isinstance(section, dict):
                    description_parts.extend(
                        [str(section.get("text") or ""), str(section.get("content") or "")]
                    )
            location_values = normalize_locations(locations)
            jobs.append(
                Job(
                    id=f"lever:{target.slug}:{source_id}",
                    company=target.name,
                    title=title,
                    application_url=url,
                    provider=self.name,
                    source_job_id=source_id,
                    locations=location_values,
                    posted_at=iso_datetime(row.get("createdAt")),
                    description=" ".join(
                        part for part in description_parts if part
                    ).strip(),
                    remote=any("remote" in value.casefold() for value in location_values),
                    metadata={"board": target.slug},
                )
            )
        return jobs
