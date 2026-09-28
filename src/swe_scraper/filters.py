"""Generic software internship filters without candidate-specific assumptions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Job

INTERNSHIP = re.compile(r"\b(?:intern|internship|co[ -]?op)\b", re.I)
EARLY_CAREER = re.compile(
    r"\b(?:student|campus|university|graduate|grad|new[ -]?grad|"
    r"early[ -]?career|apprentice|trainee|summer|spring|fall|autumn|winter|20\d{2})\b",
    re.I,
)
SOFTWARE = re.compile(
    r"\b(?:software[ -]+(?:engineer(?:ing)?|develop(?:ment|er)|programmer|architect|"
    r"intern(?:ship)?|co[ -]?op)|developer|programmer|SWE|SDE|firmware|devops|"
    r"site reliability|SRE|back[ -]?end|front[ -]?end|full[ -]?stack|"
    r"web[ -]+(?:develop(?:ment|er)|engineer(?:ing)?)|"
    r"(?:data|machine learning|AI|cloud|platform|infrastructure|mobile|security|systems?)"
    r"[ -]+(?:engineer(?:ing)?|development|developer))\b",
    re.I,
)

_LOCATION_QUERY_ALIASES = {
    "ny": ("ny", "nyc", "new york"),
    "nyc": ("nyc", "new york city", "new york ny"),
    "sf": ("sf", "san francisco"),
}


def _term_pattern(value: str) -> re.Pattern[str]:
    """Treat punctuation-bearing terms as whole tokens, not substrings."""
    return re.compile(rf"(?<![\w.+#]){re.escape(value.strip())}(?![\w.+#])", re.I)


ADJACENT = re.compile(
    r"\b(?:data|analytics|technology|computer science|machine learning|"
    r"artificial intelligence|AI|cybersecurity|cyber security|information security|"
    r"quantitative|automation|cloud|platform|infrastructure)\b",
    re.I,
)


def is_swe_internship(title: str) -> bool:
    return bool(INTERNSHIP.search(title or "") and SOFTWARE.search(title or ""))


def is_potential_internship_summary(title: object) -> bool:
    """Keep uncertain list titles so a detail page can settle the role."""
    if not isinstance(title, str) or not title.strip():
        return True
    return bool(INTERNSHIP.search(title) or EARLY_CAREER.search(title))


def filter_jobs(
    jobs: Iterable[Job],
    *,
    locations: Iterable[str] = (),
    include_keywords: Iterable[str] = (),
    exclude_keywords: Iterable[str] = (),
    include_adjacent: bool = False,
    filter_swe: bool = True,
) -> list[Job]:
    """Filter jobs using explicit public CLI options only."""
    wanted_locations = tuple(
        tuple(
            _term_pattern(alias)
            for alias in _LOCATION_QUERY_ALIASES.get(value.strip().casefold(), (value,))
        )
        for value in locations
        if value and value.strip()
    )
    required = tuple(
        _term_pattern(value) for value in include_keywords if value and value.strip()
    )
    excluded = tuple(
        _term_pattern(value) for value in exclude_keywords if value and value.strip()
    )
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
        if filter_swe and not is_swe_internship(job.title) and not adjacent:
            continue
        if wanted_locations and not any(
            pattern.search(location_text)
            for aliases in wanted_locations
            for pattern in aliases
        ):
            continue
        if required and not all(pattern.search(searchable) for pattern in required):
            continue
        if excluded and any(pattern.search(searchable) for pattern in excluded):
            continue
        kept.append(job)
    return kept
