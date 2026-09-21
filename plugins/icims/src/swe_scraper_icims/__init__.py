"""Experimental, public-only iCIMS provider plugin."""

from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from swe_scraper.models import Job
from swe_scraper.normalize import iso_datetime, normalize_locations
from swe_scraper.providers.base import HttpClient, Target

__version__ = "0.1.0"


def _plain_text(value: object) -> str:
    clean = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html_module.unescape(clean).split())


class _PortalParser(HTMLParser):
    """Collect the small, explicit HTML surface used by public iCIMS pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, tuple[str, ...]]] = []
        self.json_ld: list[str] = []
        self._script_parts: list[str] | None = None
        self._capture: str = ""
        self._capture_depth = 0
        self.text: dict[str, list[str]] = {
            "title": [],
            "description": [],
            "location": [],
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        rel = tuple(values.get("rel", "").casefold().split())
        if tag in {"a", "link"} and values.get("href"):
            self.links.append((values["href"], rel))
        if tag == "script" and values.get("type", "").casefold() == "application/ld+json":
            self._script_parts = []

        class_names = set(values.get("class", "").split())
        itemprop = values.get("itemprop", "")
        if tag == "h1" or class_names & {"iCIMS_Header", "header-text"}:
            self._capture = "title"
            self._capture_depth = 1
        elif itemprop == "description" or class_names & {
            "iCIMS_JobContent",
            "job-description",
        }:
            self._capture = "description"
            self._capture_depth = 1
        elif itemprop == "jobLocation" or "iCIMS_JobHeaderField_location" in class_names:
            self._capture = "location"
            self._capture_depth = 1
        elif self._capture:
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_parts is not None:
            self.json_ld.append("".join(self._script_parts))
            self._script_parts = None
        if self._capture:
            self._capture_depth -= 1
            if self._capture_depth <= 0:
                self._capture = ""

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
        if self._capture and data.strip():
            self.text[self._capture].append(data)


def _parse_html(value: str) -> _PortalParser:
    parser = _PortalParser()
    parser.feed(value)
    parser.close()
    return parser


def _same_origin(left: str, right: str) -> bool:
    first = urlsplit(left)
    second = urlsplit(right)
    return (
        first.scheme.casefold(),
        (first.hostname or "").casefold(),
        first.port,
    ) == (
        second.scheme.casefold(),
        (second.hostname or "").casefold(),
        second.port,
    )


def _job_id(url: str) -> str:
    match = re.search(r"/jobs/(\d+)(?:/|$)", urlsplit(url).path, re.IGNORECASE)
    return match.group(1) if match else ""


class IcimsProvider:
    """Read public iCIMS HTML without attempting to bypass access controls."""

    name = "icims"
    required_options = ("search_url",)

    def validate_target(self, target: Target) -> None:
        search_url = str(target.options.get("search_url") or "").strip()
        parsed = urlsplit(search_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("iCIMS target requires an HTTPS search_url")
        expected_host = target.slug.casefold().strip()
        if (parsed.hostname or "").casefold() != expected_host:
            raise ValueError("iCIMS search_url must use the same origin as the target slug")

    def discover_page(self, html: str, current_url: str) -> tuple[tuple[str, ...], str]:
        document = _parse_html(html)
        links: set[str] = set()
        for href, _rel in document.links:
            candidate = urljoin(current_url, href)
            if _same_origin(current_url, candidate) and _job_id(candidate):
                links.add(candidate)

        next_url = ""
        for href, rel in document.links:
            if "next" not in rel:
                continue
            candidate = urljoin(current_url, href)
            if _same_origin(current_url, candidate):
                next_url = candidate
                break
        return tuple(sorted(links)), next_url

    @staticmethod
    def _job_posting_json(html: str) -> Mapping[str, Any] | None:
        document = _parse_html(html)
        for script in document.json_ld:
            try:
                value = json.loads(script)
            except (TypeError, json.JSONDecodeError):
                continue
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    continue
                graph = candidate.get("@graph")
                nested = graph if isinstance(graph, list) else [candidate]
                for item in nested:
                    if isinstance(item, Mapping) and item.get("@type") == "JobPosting":
                        return item
        return None

    def parse_job_page(self, html: str, url: str, target: Target) -> Job:
        data = self._job_posting_json(html)
        document = _parse_html(html)
        if data is not None:
            title = str(data.get("title") or "").strip()
            company_raw = data.get("hiringOrganization")
            company = (
                str(company_raw.get("name") or "").strip()
                if isinstance(company_raw, Mapping)
                else target.name
            )
            identifier = data.get("identifier")
            source_id = (
                str(identifier.get("value") or "").strip()
                if isinstance(identifier, Mapping)
                else str(identifier or "").strip()
            )
            locations: list[str] = []
            raw_locations = data.get("jobLocation")
            for raw_location in (
                raw_locations if isinstance(raw_locations, list) else [raw_locations]
            ):
                if not isinstance(raw_location, Mapping):
                    continue
                address = raw_location.get("address")
                if not isinstance(address, Mapping):
                    continue
                parts = [
                    str(address.get(key) or "").strip()
                    for key in ("addressLocality", "addressRegion", "addressCountry")
                ]
                locations.append(", ".join(part for part in parts if part))
            description = _plain_text(data.get("description"))
            posted_at = iso_datetime(data.get("datePosted"))
        else:
            title = _plain_text(" ".join(document.text["title"]))
            company = target.name
            source_id = _job_id(url)
            description = _plain_text(" ".join(document.text["description"]))
            location = _plain_text(" ".join(document.text["location"]))
            locations = [location] if location else []
            posted_at = ""

        source_id = source_id or _job_id(url)
        if not title or not source_id:
            raise ValueError(f"iCIMS job page is missing title or job id: {url}")
        normalized_locations = normalize_locations(locations)
        return Job(
            id=f"icims:{target.slug}:{source_id}",
            company=company or target.name,
            title=title,
            application_url=url,
            provider=self.name,
            source_job_id=source_id,
            locations=normalized_locations,
            posted_at=posted_at,
            description=description,
            remote="remote" in " ".join(normalized_locations).casefold(),
            metadata={"portal": target.slug, "experimental": True},
        )

    def fetch(self, target: Target, client: HttpClient) -> list[Job]:
        self.validate_target(target)
        current_url = str(target.options["search_url"])
        max_pages = int(target.options.get("max_pages", 10))
        if not 1 <= max_pages <= 100:
            raise ValueError("iCIMS max_pages must be between 1 and 100")
        visited_pages: set[str] = set()
        job_urls: set[str] = set()
        for _ in range(max_pages):
            if current_url in visited_pages:
                raise RuntimeError("iCIMS pagination repeated a page")
            visited_pages.add(current_url)
            links, next_url = self.discover_page(client.get_text(current_url), current_url)
            job_urls.update(links)
            if not next_url:
                break
            current_url = next_url
        else:
            raise RuntimeError("iCIMS pagination exceeded max_pages")
        return [
            self.parse_job_page(client.get_text(url), url, target)
            for url in sorted(job_urls)
        ]


__all__ = ["IcimsProvider", "__version__"]
