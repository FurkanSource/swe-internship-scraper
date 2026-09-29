"""Generate synthetic identity-contract regressions, not a field accuracy estimate."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "fixtures" / "dedupe_benchmark.json"


def record(index: int) -> dict[str, Any]:
    return {
        "id": f"oracle:tenant-a:CX_1:{index}",
        "company": "Example Systems",
        "title": "Software Engineer Intern Summer 2027",
        "application_url": f"https://tenant-a.example.test/CX_1/job/{index}",
        "provider": "oracle",
        "source_job_id": str(index),
        "locations": ["New York, NY"],
        "metadata": {"source_namespace": "https://tenant-a.example.test/CX_1"},
    }


def build() -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    for index in range(100):
        left = record(1000 + index)
        right = copy.deepcopy(left)
        pattern = index % 4
        if pattern == 0:
            case = "exact URL with tracking and changed provider"
            right.update(
                provider="greenhouse",
                source_job_id=f"mirror-{index}",
                metadata={},
                application_url=left["application_url"] + "?utm_source=feed",
            )
        elif pattern == 1:
            case = "explicit scoped identity with changed direct URL"
            right["application_url"] = f"https://careers.example.test/requisition/{index}"
        elif pattern == 2:
            case = "canonical Greenhouse URL alias"
            left["application_url"] = f"https://boards.greenhouse.io/example/jobs/{index}"
            right["application_url"] = (
                f"https://job-boards.greenhouse.io/example/jobs/{index}/?gh_src=feed"
            )
            left["metadata"] = right["metadata"] = {}
        else:
            case = "exact URL preserves updated title and location"
            right.update(
                title="Software Engineering Internship Summer 2027",
                locations=["NYC"],
                description="Updated official posting description.",
            )
        pairs.append({"label": "duplicate", "case": case, "left": left, "right": right})

    for index in range(250):
        left = record(2000 + index)
        right = copy.deepcopy(left)
        right.update(
            id=f"oracle:other:{index}",
            source_job_id=f"other-{index}",
            application_url=f"https://other.example.test/job/{index}",
        )
        pattern = index % 10
        if pattern == 0:
            case = "same raw ID across employer tenants"
            right.update(
                source_job_id=left["source_job_id"],
                company="Another Employer",
                metadata={"source_namespace": "https://other.example.test/CX_1"},
            )
        elif pattern == 1:
            case = "different requisitions identical company title city"
        elif pattern == 2:
            case = "remote roles with different country restrictions"
            left.update(locations=["Remote - US"], remote=True)
            right.update(locations=["Remote - UK"], remote=True)
        elif pattern == 3:
            case = "same raw ID without known namespace"
            left["metadata"] = right["metadata"] = {}
            right["source_job_id"] = left["source_job_id"]
        elif pattern == 4:
            case = "source IDs are case sensitive"
            left["source_job_id"] = f"Req-{index}"
            right["source_job_id"] = f"req-{index}"
        elif pattern == 5:
            case = "same raw ID different board within tenant"
            right.update(
                source_job_id=left["source_job_id"],
                metadata={"source_namespace": "https://tenant-a.example.test/CX_2"},
            )
        elif pattern == 6:
            case = "title and city aliases without identity evidence"
            right.update(
                company="Example Systems, Inc.",
                provider="lever",
                title="Summer 2027 Software Developer Internship",
                locations=["NYC"],
            )
        elif pattern == 7:
            case = "different recruiting year"
            right["title"] = "Software Engineer Intern Summer 2028"
        elif pattern == 8:
            case = "overlapping multi-city requisitions"
            right["locations"] = ["New York, NY", "Boston, MA"]
        else:
            case = "same ID and namespace string in different providers"
            right.update(source_job_id=left["source_job_id"], provider="workday")
        pairs.append({"label": "distinct", "case": case, "left": left, "right": right})
    return {
        "description": (
            "Synthetic identity-contract regression suite with 14 pattern families. "
            "Labels express explicit identity equivalence, not human judgments of semantic "
            "duplicates. Passing does not measure real-world recall or "
            "false-merge prevalence."
        ),
        "duplicate_pairs": 100,
        "hard_negative_pairs": 250,
        "pairs": pairs,
    }


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
