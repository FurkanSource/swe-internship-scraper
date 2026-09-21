"""Generate the sanitized deterministic deduplication benchmark fixture."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "fixtures" / "dedupe_benchmark.json"


def record(
    identity: str,
    provider: str,
    company: str,
    title: str,
    location: str,
) -> dict[str, object]:
    return {
        "id": f"{provider}:{identity}",
        "company": company,
        "title": title,
        "application_url": f"https://jobs.example.test/{provider}/{identity}",
        "provider": provider,
        "source_job_id": identity,
        "locations": [location],
    }


def build() -> dict[str, object]:
    pairs: list[dict[str, object]] = []
    for index in range(100):
        identity = f"duplicate-{index:03d}"
        pairs.append(
            {
                "label": "duplicate",
                "case": "canonical company, title alias, and location alias",
                "left": record(
                    f"{identity}-a",
                    "greenhouse",
                    f"Example Systems {index}, Inc.",
                    "Software Engineering Intern - Summer 2027",
                    "New York, NY",
                ),
                "right": record(
                    f"{identity}-b",
                    "lever",
                    f"Example Systems {index}",
                    "Software Developer Intern Summer 2027",
                    "NYC",
                ),
            }
        )

    for index in range(100):
        identity = f"negative-year-{index:03d}"
        pairs.append(
            {
                "label": "distinct",
                "case": "different recruiting year",
                "left": record(
                    f"{identity}-a",
                    "greenhouse",
                    f"Year Boundary Labs {index}",
                    "Software Engineer Intern Summer 2027",
                    "New York, NY",
                ),
                "right": record(
                    f"{identity}-b",
                    "lever",
                    f"Year Boundary Labs {index}, Inc.",
                    "Software Engineering Intern Summer 2028",
                    "NYC",
                ),
            }
        )

    for index in range(75):
        identity = f"negative-location-{index:03d}"
        pairs.append(
            {
                "label": "distinct",
                "case": "different physical location",
                "left": record(
                    f"{identity}-a",
                    "greenhouse",
                    f"Location Labs {index}",
                    "Software Engineer Intern Summer 2027",
                    "New York, NY",
                ),
                "right": record(
                    f"{identity}-b",
                    "oracle",
                    f"Location Labs {index}",
                    "Software Engineering Intern Summer 2027",
                    "San Francisco, CA",
                ),
            }
        )

    for index in range(75):
        identity = f"negative-role-{index:03d}"
        pairs.append(
            {
                "label": "distinct",
                "case": "different role family",
                "left": record(
                    f"{identity}-a",
                    "smartrecruiters",
                    f"Role Boundary Labs {index}",
                    "Software Engineer Intern Summer 2027",
                    "Remote",
                ),
                "right": record(
                    f"{identity}-b",
                    "ashby",
                    f"Role Boundary Labs {index}",
                    "Data Science Intern Summer 2027",
                    "Remote - US",
                ),
            }
        )
    return {
        "description": (
            "Synthetic, sanitized labeled pairs for regression testing. "
            "No person, employer posting, or applicant data is included."
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
