"""Validation for public JSON exports and legacy scanner handoffs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Job


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    jobs: int
    errors: tuple[str, ...]


def validate_payload(payload: Any) -> ValidationReport:
    rows: object
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("jobs")
        if rows is None:
            rows = payload.get("candidates")
    else:
        return ValidationReport(False, 0, ("top-level value must be an object or list",))
    if not isinstance(rows, list):
        return ValidationReport(
            False, 0, ("payload must contain a jobs or candidates list",)
        )
    errors: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"jobs[{index}] must be an object")
            continue
        try:
            Job.from_mapping(row)
        except (TypeError, ValueError) as exc:
            errors.append(f"jobs[{index}]: {exc}")
    return ValidationReport(not errors, len(rows), tuple(errors))


def validate_file(path: Path | str) -> ValidationReport:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return ValidationReport(False, 0, (str(exc),))
    return validate_payload(payload)
