import tempfile
import unittest
from pathlib import Path
from unittest import mock

from swe_scraper import cli
from swe_scraper.config import load_targets
from swe_scraper.models import Job, ProviderFailure, ScanResult
from swe_scraper.providers.base import Target
from swe_scraper.scanner import scan_targets
from swe_scraper.validation import validate_file, validate_payload
from swe_scraper.watch import load_seen, unseen_jobs


def sample_job(identifier="1"):
    return Job(
        id=f"greenhouse:acme:{identifier}",
        company="Acme",
        title="Software Engineering Intern",
        application_url=f"https://job-boards.greenhouse.io/acme/jobs/{identifier}",
        provider="greenhouse",
        source_job_id=identifier,
    )


class ScannerAndCliTests(unittest.TestCase):
    def test_bundled_targets_and_provider_selection(self):
        targets = load_targets(providers=["lever"])
        self.assertTrue(targets)
        self.assertEqual({target.provider for target in targets}, {"lever"})

    def test_scanner_isolates_provider_failure(self):
        class Provider:
            def fetch(self, target, client):
                if target.slug == "bad":
                    raise RuntimeError("board unavailable")
                return [sample_job()]

        targets = [Target("greenhouse", "Acme", "ok"), Target("greenhouse", "Bad", "bad")]
        with mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()):
            result = scan_targets(targets, client=object(), max_workers=2)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("board unavailable", result.errors[0].error)

    def test_validate_public_and_legacy_payloads(self):
        self.assertTrue(validate_payload({"jobs": [sample_job().to_dict()]}).valid)
        legacy = sample_job().to_dict()
        legacy["company_name"] = legacy.pop("company")
        legacy["url"] = legacy.pop("application_url")
        self.assertTrue(validate_payload({"candidates": [legacy]}).valid)
        self.assertFalse(validate_payload({"jobs": [{"id": "missing"}]}).valid)

    def test_scan_command_writes_valid_json(self):
        result = ScanResult.from_iterables(
            [sample_job()],
            [ProviderFailure("lever", "Broken", "broken", "timeout")],
        )
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "jobs.json"
            with mock.patch("swe_scraper.cli._run_scan", return_value=result):
                code = cli.main(["scan", "--output", str(output)])
            report = validate_file(output)
        self.assertEqual(code, 0)
        self.assertTrue(report.valid)

    def test_scan_command_infers_csv_from_extension(self):
        result = ScanResult.from_iterables([sample_job()])
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "jobs.csv"
            with mock.patch("swe_scraper.cli._run_scan", return_value=result):
                code = cli.main(["scan", "--output", str(output)])
            content = output.read_text(encoding="utf-8-sig")
        self.assertEqual(code, 0)
        self.assertIn("company,title", content)
        self.assertIn("Acme,Software Engineering Intern", content)

    def test_watch_once_updates_seen_state(self):
        result = ScanResult.from_iterables([sample_job()])
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "jobs.json"
            state = Path(td) / "seen.json"
            with mock.patch("swe_scraper.cli._run_scan", return_value=result):
                code = cli.main(
                    ["watch", "--once", "--output", str(output), "--state", str(state)]
                )
            self.assertEqual(len(unseen_jobs(result.jobs, load_seen(state))), 0)
        self.assertEqual(code, 0)

    def test_parser_exposes_scraper_commands(self):
        parser = cli.build_parser()
        help_text = parser.format_help()
        for command in ("scan", "validate", "watch", "health"):
            self.assertIn(command, help_text)
        self.assertNotIn("tracker", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
