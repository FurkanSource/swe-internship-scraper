"""SmartRecruiters public Posting API adapter."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

from ..models import Job
from ..normalize import iso_datetime, normalize_locations
from ._reliability import (
    PaginationTotalChanged,
    detail_workers,
    ordered_details,
    restart_on_total_change,
)
from .base import HttpClient, Target


def _text(value: Any) -> str:
    clean = re.sub(r"(?is)<(script|style).*?</\1>", " ", str(value or ""))
    clean = re.sub(r"<[^>]+>", " ", clean)
    return " ".join(html.unescape(clean).split())


def _locations(payload: dict[str, Any]) -> tuple[str, ...]:
    location = payload.get("location") or {}
    if not isinstance(location, dict):
        return normalize_locations([location])
    parts = [
        location.get("city"),
        location.get("region"),
        location.get("country"),
    ]
    primary = ", ".join(str(value).strip() for value in parts if value)
    additional = payload.get("otherLocations") or []
    values: list[str] = [primary]
    for item in additional:
        if isinstance(item, dict):
            values.append(
                ", ".join(
                    str(item.get(name) or "").strip()
                    for name in ("city", "region", "country")
                    if item.get(name)
                )
            )
    return normalize_locations(values)


class SmartRecruitersProvider:
    name = "smartrecruiters"
    required_options: tuple[str, ...] = ()

    def validate_target(self, target: Target) -> None:
        if not target.slug.strip():
            raise ValueError("SmartRecruiters target requires a company identifier")
        page_size = int(target.options.get("page_size", 100))
        max_pages = int(target.options.get("max_pages", 100))
        if not 1 <= page_size <= 100:
            raise ValueError("SmartRecruiters page_size must be between 1 and 100")
        if not 1 <= max_pages <= 100:
            raise ValueError("SmartRecruiters max_pages must be between 1 and 100")
        detail_workers(target)

    def fetch(self, target: Target, client: HttpClient) -> list[Job]:
        self.validate_target(target)
        company = urllib.parse.quote(target.slug, safe="")
        endpoint = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
        workers = detail_workers(target)
        posting_ids = restart_on_total_change(
            lambda: self._fetch_posting_ids(target, client, endpoint)
        )

        def fetch_detail(source_id: str) -> Job:
            detail = client.get_json(f"{endpoint}/{urllib.parse.quote(source_id, safe='')}")
            parsed = self.parse_detail(target, detail)
            if parsed is None:
                raise RuntimeError(
                    f"SmartRecruiters posting '{source_id}' returned malformed details"
                )
            return parsed

        return ordered_details(fetch_detail, posting_ids, workers)

    def _fetch_posting_ids(
        self, target: Target, client: HttpClient, endpoint: str
    ) -> list[str]:
        page_size = int(target.options.get("page_size", 100))
        max_pages = int(target.options.get("max_pages", 100))
        posting_ids: list[str] = []
        seen_ids: set[str] = set()
        expected_total: int | None = None
        offset = 0
        complete = False
        for _page in range(max_pages):
            payload = client.get_json(
                endpoint,
                params={
                    "destination": "PUBLIC",
                    "limit": page_size,
                    "offset": offset,
                },
            )
            if not isinstance(payload, dict) or not isinstance(
                payload.get("content"), list
            ):
                raise RuntimeError(
                    f"SmartRecruiters board '{target.name}' returned a malformed page"
                )
            total = payload.get("totalFound")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise RuntimeError(
                    f"SmartRecruiters board '{target.name}' omitted totalFound"
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise PaginationTotalChanged(
                    f"SmartRecruiters board '{target.name}' changed totalFound "
                    "during pagination"
                )
            rows = payload["content"]
            page_ids = [
                str(row.get("id") or "").strip()
                for row in rows
                if isinstance(row, dict) and str(row.get("id") or "").strip()
            ]
            if len(page_ids) != len(rows):
                raise RuntimeError("SmartRecruiters page contains a job without an ID")
            if len(set(page_ids)) != len(page_ids) or any(
                value in seen_ids for value in page_ids
            ):
                raise RuntimeError(
                    f"SmartRecruiters board '{target.name}' repeated a pagination page"
                )
            posting_ids.extend(page_ids)
            seen_ids.update(page_ids)
            offset += len(rows)
            if offset > total:
                raise RuntimeError("SmartRecruiters page exceeded totalFound")
            if offset >= total:
                complete = True
                break
            if not rows:
                raise RuntimeError(
                    f"SmartRecruiters board '{target.name}' pagination stopped "
                    "before totalFound"
                )
        if not complete:
            raise RuntimeError(
                f"SmartRecruiters board '{target.name}' exceeded configured "
                "pagination limit"
            )

        return posting_ids

    def parse_detail(self, target: Target, payload: Any) -> Job | None:
        if not isinstance(payload, dict):
            return None
        source_id = str(payload.get("id") or "").strip()
        title = str(payload.get("name") or payload.get("title") or "").strip()
        if not source_id or not title:
            return None
        sections = (payload.get("jobAd") or {}).get("sections") or {}
        chunks: list[str] = []
        if isinstance(sections, dict):
            for section in sections.values():
                if isinstance(section, dict) and section.get("text"):
                    chunks.append(str(section["text"]))
        application_url = str(
            payload.get("applyUrl")
            or payload.get("postingUrl")
            or f"https://jobs.smartrecruiters.com/{target.slug}/{source_id}"
        ).strip()
        locations = _locations(payload)
        location_type = str(payload.get("locationType") or "").casefold()
        return Job(
            id=f"smartrecruiters:{target.slug}:{source_id}",
            company=target.name,
            title=title,
            application_url=application_url,
            provider=self.name,
            source_job_id=source_id,
            locations=locations,
            posted_at=iso_datetime(payload.get("releasedDate")),
            description=_text(" ".join(chunks)),
            remote=location_type == "remote"
            or any("remote" in value.casefold() for value in locations),
            metadata={
                "company_identifier": target.slug,
                "reference_number": str(payload.get("refNumber") or ""),
                "compensation": payload.get("compensation"),
            },
        )
