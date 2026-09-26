"""Generic software internship filters without candidate-specific assumptions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Job

INTERNSHIP = re.compile(r"\b(?:intern|internship|co[ -]?op)\b", re.I)
SOFTWARE = re.compile(
    r"\b(?:software|developer|programmer|SWE|SDE|firmware|devops|"
    r"site reliability|SRE|back[ -]?end|front[ -]?end|full[ -]?stack|"
    r"(?:data|machine learning|AI|cloud|platform|infrastructure|mobile|security|systems?)"
    r"[ -]+(?:engineer(?:ing)?|development|developer))\b",
    re.I,
)
ADJACENT = re.compile(
    r"\b(?:data|analytics|technology|computer science|machine learning|"
    r"artificial intelligence|AI|cybersecurity|cyber security|information security|"
    r"quantitative|automation|cloud|platform|infrastructure)\b",
    re.I,
)


def is_swe_internship(title: str) -> bool:
    return bool(INTERNSHIP.search(title or "") and SOFTWARE.search(title or ""))


def filter_jobs(
    jobs: Iterable[Job],
    *,
    locations: Iterable[str] = (),
    include_keywords: Iterable[str] = (),
    exclude_keywords: Iterable[str] = (),
    include_adjacent: bool = False,
) -> list[Job]:
    """Filter jobs using explicit public CLI options only."""
    wanted_locations = tuple(value.casefold() for value in locations if value)
    required = tuple(value.casefold() for value in include_keywords if value)
    excluded = tuple(value.casefold() for value in exclude_keywords if value)
    kept: list[Job] = []
    for job in jobs:
        searchable = " ".join(
            (job.company, job.title, " ".join(job.locations), job.description)
        ).casefold()
        location_text = " ".join(job.locations).casefold()
        if job.remote:
            location_text += " remote"
        adjacent = (
            include_adjacent and INTERNSHIP.search(job.title) and ADJACENT.search(job.title)
        )
        if not is_swe_internship(job.title) and not adjacent:
            continue
        if wanted_locations and not any(
            value in location_text for value in wanted_locations
        ):
            continue
        if required and not all(value in searchable for value in required):
            continue
        if excluded and any(value in searchable for value in excluded):
            continue
        kept.append(job)
    return kept
