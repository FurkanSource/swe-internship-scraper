"""Board observations are the source of truth for public-list closure decisions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.board_results import target_key
from swe_scraper.cli import main
from swe_scraper.models import Job
from swe_scraper.providers.base import Target
from swe_scraper.scanner import scan_targets


def _job(source_id: str) -> Job:
    return Job(
        id=f"oracle:{source_id}",
        company="Example",
        title="Software Engineer Intern Summer 2027",
        application_url=f"https://example.com/jobs/{source_id}",
        provider="oracle",
        source_job_id=source_id,
    )


class BoardResultsContractTests(unittest.TestCase):
    def test_target_key_scopes_shared_oracle_slug(self) -> None:
        one = Target("oracle", "A", "cx_1", {"origin": "https://a.example", "site": "1"})
        renamed = Target(
            "oracle",
            "Renamed",
            "cx_1",
            {"origin": "https://a.example/", "site": "1", "max_pages": 2},
        )
        other = Target("oracle", "B", "cx_1", {"origin": "https://b.example", "site": "1"})
        self.assertEqual(target_key(one), target_key(renamed))
        self.assertNotEqual(target_key(one), target_key(other))

    def test_board_results_include_complete_empty_failed_and_job_sources(self) -> None:
        targets = [
            Target("oracle", "A", "cx_1", {"origin": "https://a.example", "site": "1"}),
            Target("oracle", "B", "cx_1", {"origin": "https://b.example", "site": "1"}),
            Target("oracle", "C", "cx_1", {"origin": "https://c.example", "site": "1"}),
        ]

        class Provider:
            def validate_target(self, target: Target) -> None:
                pass

            def fetch(self, target: Target, client: object) -> list[Job]:
                if target.name == "A":
                    return [_job("123")]
                if target.name == "B":
                    return []
                raise RuntimeError("board unavailable")

        with patch("swe_scraper.scanner.get_provider", return_value=Provider()):
            result = scan_targets(
                targets, client=object(), include_board_results=True, max_workers=1
            ).to_dict()
        boards = result["board_results"]
        self.assertEqual(
            [board["status"] for board in boards], ["complete", "complete", "failed"]
        )
        self.assertEqual([board["job_count"] for board in boards], [1, 0, 0])
        self.assertEqual(len({board["target_key"] for board in boards}), 3)
        self.assertEqual(boards[0]["sources"], result["jobs"][0]["sources"])
        self.assertEqual(boards[1]["sources"], [])
        self.assertEqual(result["schema_version"], 2)

    def test_duplicate_target_key_rejected_before_fetch(self) -> None:
        class Provider:
            def validate_target(self, target: Target) -> None:
                pass

            def fetch(self, target: Target, client: object) -> list[Job]:
                raise AssertionError("should not fetch")

        targets = [
            Target("oracle", "A", "cx_1", {"origin": "https://a.example"}),
            Target("oracle", "Renamed", "cx_1", {"origin": "https://a.example/"}),
        ]
        with (
            patch("swe_scraper.scanner.get_provider", return_value=Provider()),
            self.assertRaisesRegex(ValueError, "duplicate target_key"),
        ):
            scan_targets(targets, client=object(), include_board_results=True)

    def test_default_json_contract_omits_board_results(self) -> None:
        class Provider:
            def validate_target(self, target: Target) -> None:
                pass

            def fetch(self, target: Target, client: object) -> list[Job]:
                return []

        with patch("swe_scraper.scanner.get_provider", return_value=Provider()):
            result = scan_targets(
                [Target("oracle", "A", "cx_1", {"origin": "https://a.example"})],
                client=object(),
            )
        self.assertNotIn("board_results", result.to_dict())

    def test_board_results_reject_csv_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "jobs.csv"
            with self.assertRaises(SystemExit) as raised:
                main(
                    ["scan", "--quick", "--include-board-results", "--output", str(output)]
                )
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(output.exists())
