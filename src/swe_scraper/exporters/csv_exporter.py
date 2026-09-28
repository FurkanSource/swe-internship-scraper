"""Spreadsheet-oriented CSV export with best-effort formula prefixing."""

from __future__ import annotations

import csv
import os
from pathlib import Path

from ..models import ScanResult

FIELDS = (
    "id",
    "company",
    "title",
    "locations",
    "application_url",
    "provider",
    "source_job_id",
    "posted_at",
    "remote",
    "description",
)


def _spreadsheet_cell(value: object) -> object:
    """Reduce initial formula interpretation; apostrophes alter cell contents."""
    if isinstance(value, str):
        content = value.lstrip(" \t\r\n")
        if content and content[0] in "=+-@":
            return "'" + value
    return value


def write_csv(result: ScanResult, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for job in result.jobs:
            row = job.to_dict()
            row["locations"] = "; ".join(job.locations)
            writer.writerow(
                {field: _spreadsheet_cell(row.get(field, "")) for field in FIELDS}
            )
    os.replace(temporary, target)
    return target
