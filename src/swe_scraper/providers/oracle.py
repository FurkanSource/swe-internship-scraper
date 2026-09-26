"""Oracle Recruiting Candidate Experience public REST adapter."""

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


class OracleProvider:
    name = "oracle"
    required_options = ("origin",)

    def validate_target(self, target: Target) -> None:
        origin = str(target.options.get("origin") or "").rstrip("/")
        try:
            parsed = urllib.parse.urlsplit(origin)
        except ValueError as exc:
            raise ValueError("Oracle target requires a valid HTTPS origin") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Oracle target requires an HTTPS origin without a path")
        if not target.slug.strip():
            raise ValueError("Oracle target requires a siteNumber slug")
        page_size = int(target.options.get("page_size", 25))
        max_pages = int(target.options.get("max_pages", 200))
        if not 1 <= page_size <= 100:
            raise ValueError("Oracle page_size must be between 1 and 100")
        if not 1 <= max_pages <= 200:
            raise ValueError("Oracle max_pages must be between 1 and 200")
        detail_workers(target)

    def _url(self, origin: str, resource: str, params: dict[str, str]) -> str:
        query = urllib.parse.urlencode(params, safe=';,"')
        return f"{origin}/hcmRestApi/resources/latest/{resource}?{query}"

    def _headers(self, origin: str, target: Target) -> dict[str, str]:
        locale = str(target.options.get("locale") or "en")
        return {
            "Accept-Language": locale,
            "Ora-Irc-Language": locale,
            "REST-Framework-Version": "4",
            "Referer": (
                f"{origin}/hcmUI/CandidateExperience/{locale}/sites/{target.slug}/jobs"
            ),
        }

    def fetch(self, target: Target, client: HttpClient) -> list[Job]:
        self.validate_target(target)
        origin = str(target.options["origin"]).rstrip("/")
        headers = self._headers(origin, target)
        workers = detail_workers(target)
        summaries = restart_on_total_change(lambda: self._fetch_summaries(target, client))

        def fetch_detail(summary: dict[str, Any]) -> Job:
            source_id = self._source_id(summary)
            detail_url = self._url(
                origin,
                "recruitingCEJobRequisitionDetails",
                {
                    "expand": "all",
                    "finder": (f'ById;Id="{source_id}",siteNumber="{target.slug}"'),
                },
            )
            detail_payload = client.get_json(detail_url, headers=headers)
            detail_rows = (
                detail_payload.get("items") if isinstance(detail_payload, dict) else None
            )
            if (
                not isinstance(detail_rows, list)
                or not detail_rows
                or not isinstance(detail_rows[0], dict)
            ):
                raise RuntimeError(
                    f"Oracle requisition '{source_id}' returned malformed details"
                )
            return self.parse_detail(target, summary, detail_rows[0])

        return ordered_details(fetch_detail, summaries, workers)

    def _fetch_summaries(self, target: Target, client: HttpClient) -> list[dict[str, Any]]:
        origin = str(target.options["origin"]).rstrip("/")
        page_size = int(target.options.get("page_size", 25))
        max_pages = int(target.options.get("max_pages", 200))
        headers = self._headers(origin, target)
        summaries: list[dict[str, Any]] = []
        seen: set[str] = set()
        expected_total: int | None = None
        offset = 0
        complete = False
        for _page in range(max_pages):
            finder = f"findReqs;siteNumber={target.slug},limit={page_size},offset={offset}"
            endpoint = self._url(
                origin,
                "recruitingCEJobRequisitions",
                {
                    "onlyData": "true",
                    "expand": "requisitionList.secondaryLocations",
                    "finder": finder,
                },
            )
            payload = client.get_json(endpoint, headers=headers)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise RuntimeError(
                    f"Oracle board '{target.name}' returned a malformed page"
                )
            container = self._list_container(payload)
            rows = self._list_rows(payload)
            outer_items = payload.get("items") or []
            reported_total = None
            if outer_items and isinstance(outer_items[0], dict):
                value = outer_items[0].get("TotalJobsCount")
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                ):
                    raise RuntimeError("Oracle response has invalid TotalJobsCount")
                if isinstance(value, int) and value >= 0:
                    reported_total = value
            if reported_total is not None:
                if expected_total is None:
                    expected_total = reported_total
                elif reported_total != expected_total:
                    raise PaginationTotalChanged(
                        f"Oracle board '{target.name}' changed TotalJobsCount "
                        "during pagination"
                    )
            identifiers = [self._source_id(row) for row in rows]
            if any(not value for value in identifiers):
                raise RuntimeError(
                    f"Oracle board '{target.name}' returned a job without an ID"
                )
            if len(set(identifiers)) != len(identifiers) or any(
                value in seen for value in identifiers
            ):
                raise RuntimeError(
                    f"Oracle board '{target.name}' repeated a pagination page"
                )
            summaries.extend(rows)
            seen.update(identifiers)
            offset += len(rows)
            has_more = container.get("hasMore")
            if not isinstance(has_more, bool):
                raise RuntimeError(f"Oracle board '{target.name}' omitted hasMore")
            if expected_total is not None:
                if offset > expected_total:
                    raise RuntimeError("Oracle page exceeded TotalJobsCount")
                if offset >= expected_total:
                    if has_more is not False:
                        raise RuntimeError(
                            f"Oracle board '{target.name}' pagination remained open "
                            "after total"
                        )
                    complete = True
                    break
            elif has_more is False:
                complete = True
                break
            if not rows:
                raise RuntimeError(
                    f"Oracle board '{target.name}' pagination stopped before completion"
                )
        if not complete:
            raise RuntimeError(
                f"Oracle board '{target.name}' exceeded configured pagination limit"
            )

        return summaries

    def _list_container(self, payload: dict[str, Any]) -> dict[str, Any]:
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            nested = item.get("requisitionList")
            if isinstance(nested, dict) and isinstance(nested.get("items"), list):
                return nested
            if isinstance(nested, list):
                return {
                    "items": nested,
                    "hasMore": payload.get("hasMore"),
                }
        if all(isinstance(item, dict) for item in payload.get("items") or []):
            return {
                "items": payload.get("items") or [],
                "hasMore": payload.get("hasMore"),
            }
        raise RuntimeError("Oracle requisition response omitted requisitionList")

    def _list_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self._list_container(payload).get("items") or []
        if any(not isinstance(value, dict) for value in rows):
            raise RuntimeError("Oracle requisition list contains a malformed job")
        return [value for value in rows if isinstance(value, dict)]

    def _source_id(self, row: dict[str, Any]) -> str:
        return str(
            row.get("Id") or row.get("RequisitionNumber") or row.get("RequisitionId") or ""
        ).strip()

    def parse_detail(
        self,
        target: Target,
        summary: dict[str, Any],
        detail: dict[str, Any],
    ) -> Job:
        source_id = self._source_id(detail) or self._source_id(summary)
        title = str(
            detail.get("Title")
            or detail.get("RequisitionTitle")
            or summary.get("Title")
            or ""
        ).strip()
        if not source_id or not title:
            raise RuntimeError("Oracle requisition details omitted ID or title")
        locations: list[Any] = [
            summary.get("PrimaryLocation"),
            detail.get("PrimaryLocation"),
        ]
        for container in (summary, detail):
            secondary = container.get("secondaryLocations") or []
            if isinstance(secondary, dict):
                secondary = secondary.get("items") or []
            for item in secondary:
                if isinstance(item, dict):
                    locations.append(item.get("Name") or item.get("Location"))
        normalized = normalize_locations(locations)
        locale = str(target.options.get("locale") or "en")
        origin = str(target.options["origin"]).rstrip("/")
        description = _text(
            detail.get("ExternalDescriptionStr")
            or detail.get("ExternalResponsibilitiesStr")
            or summary.get("ExternalResponsibilitiesStr")
        )
        return Job(
            id=f"oracle:{urllib.parse.urlsplit(origin).hostname}:{target.slug}:{source_id}",
            company=target.name,
            title=title,
            application_url=(
                f"{origin}/hcmUI/CandidateExperience/{locale}/sites/"
                f"{target.slug}/job/{source_id}"
            ),
            provider=self.name,
            source_job_id=source_id,
            locations=normalized,
            posted_at=iso_datetime(detail.get("PostedDate") or summary.get("PostedDate")),
            description=description,
            remote=any("remote" in value.casefold() for value in normalized),
            metadata={
                "origin": origin,
                "site_number": target.slug,
                "posting_end_date": str(
                    detail.get("PostingEndDate") or summary.get("PostingEndDate") or ""
                ),
            },
        )
