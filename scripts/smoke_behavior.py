"""Exercise the installed package offline, outside the source checkout."""

import csv
import json
from pathlib import Path

from swe_scraper import cli
from swe_scraper.models import Job
from swe_scraper.providers.registry import DEFAULT_REGISTRY


class FixtureProvider:
    name = "release-fixture"
    required_options = ()

    def validate_target(self, target):
        if target.slug not in {"good", "broken"}:
            raise ValueError("Unknown offline fixture")

    def fetch(self, target, client):
        if target.slug == "broken":
            raise RuntimeError("Deliberate offline provider failure")
        return [
            Job(
                id="release-fixture:1",
                company="Example",
                title="Software Intern",
                provider=self.name,
                source_job_id="1",
                application_url="https://example.test/jobs/1",
                locations=("London",),
            )
        ]


def main():
    DEFAULT_REGISTRY.register(FixtureProvider())
    targets = Path("targets.json")
    targets.write_text(
        json.dumps({"release-fixture": [{"name": "Example", "slug": "good"}]}),
        encoding="utf-8",
    )
    common = ["--targets", str(targets)]
    assert cli.main(["scan", *common, "--output", "jobs.json"]) == 0
    jobs = json.loads(Path("jobs.json").read_text(encoding="utf-8"))
    assert len(jobs["jobs"]) == 1 and jobs["jobs"][0]["sources"]
    assert cli.main(["validate", "jobs.json"]) == 0
    assert cli.main(["scan", *common, "--output", "jobs.csv"]) == 0
    with Path("jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["title"] == "Software Intern"
    watch = [
        "watch",
        *common,
        "--once",
        "--state",
        "seen.json",
        "--notify-jsonl",
        "events.jsonl",
    ]
    assert cli.main(watch) == 0
    assert Path("seen.json").is_file()
    events = Path("events.jsonl").read_bytes()
    assert events
    assert cli.main(watch) == 0
    assert Path("events.jsonl").read_bytes() == events
    targets.write_text(
        json.dumps(
            {
                "release-fixture": [
                    {"name": "Good", "slug": "good"},
                    {"name": "Broken", "slug": "broken"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["scan", *common, "--strict", "--output", "partial.json"]) == 1
    partial = json.loads(Path("partial.json").read_text(encoding="utf-8"))
    assert len(partial["jobs"]) == 1 and len(partial["errors"]) == 1
    assert (
        cli.main(
            [
                "scan",
                *common,
                "--strict",
                "--include",
                "absent-term",
                "--output",
                "empty.json",
            ]
        )
        == 2
    )
    print("Installed offline scan, exports, watch, and error contracts passed")


if __name__ == "__main__":
    main()
