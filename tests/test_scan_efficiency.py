"""Full-catalog throughput and recovery contracts."""

import contextlib
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper import cli
from swe_scraper.checkpoints import ScanCheckpointStore
from swe_scraper.filters import is_potential_internship_summary
from swe_scraper.models import Job
from swe_scraper.providers.base import Target
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider
from swe_scraper.scanner import scan_targets

TITLES = {
    "1": "Senior Software Engineer",
    "2": "Sales Manager",
    "3": "Software Engineering Intern",
    "4": "Student Program",
    "5": "",
    "6": "Summer Software Engineer",
}
DETAIL_TITLES = {**TITLES, "4": "Software Intern", "5": "SWE Intern"}


def make_job(source_id="3"):
    return Job(
        id=f"example:{source_id}",
        company="Example",
        title="Software Engineering Intern",
        application_url=f"https://example.test/jobs/{source_id}",
        provider="example",
        source_job_id=source_id,
    )


class MixedBoardClient:
    def __init__(self, provider):
        self.provider = provider
        self.list_calls = 0
        self.details = []

    def get_json(self, url, **kwargs):
        if self.provider == "smartrecruiters":
            if url.endswith("/postings"):
                self.list_calls += 1
                return {
                    "totalFound": len(TITLES),
                    "content": [
                        {"id": key, "name": title} for key, title in TITLES.items()
                    ],
                }
            source_id = urllib.parse.unquote(url.rsplit("/", 1)[1])
            self.details.append(source_id)
            return {"id": source_id, "name": DETAIL_TITLES[source_id]}
        if "recruitingCEJobRequisitions?" in url:
            self.list_calls += 1
            return {
                "items": [
                    {
                        "TotalJobsCount": len(TITLES),
                        "requisitionList": {
                            "items": [
                                {"Id": key, "Title": title} for key, title in TITLES.items()
                            ],
                            "hasMore": False,
                        },
                    }
                ]
            }
        source_id = urllib.parse.unquote(url).split('Id="', 1)[1].split('"', 1)[0]
        self.details.append(source_id)
        return {"items": [{"Id": source_id, "Title": DETAIL_TITLES[source_id]}]}


class CandidateFetchTests(unittest.TestCase):
    def test_conservative_summary_decisions(self):
        for title in (
            "Software Intern",
            "Data Co-op",
            "Student Program",
            "Early Career Engineer",
            "New Graduate Developer",
            "Summer Software Engineer",
            "Apprentice Developer",
            "",
            None,
            {"unexpected": "shape"},
        ):
            with self.subTest(title=title):
                self.assertTrue(is_potential_internship_summary(title))
        for title in ("Senior Software Engineer", "Sales Manager", "Registered Nurse"):
            with self.subTest(title=title):
                self.assertFalse(is_potential_internship_summary(title))

    def test_candidates_skip_irrelevant_details_but_full_fetch_is_unchanged(self):
        for provider in (SmartRecruitersProvider(), OracleProvider()):
            with self.subTest(provider=provider.name):
                options = (
                    {"origin": "https://example.test"} if provider.name == "oracle" else {}
                )
                target = Target(provider.name, "Example", "example", options)
                baseline_client = MixedBoardClient(provider.name)
                baseline = provider.fetch(target, baseline_client)
                self.assertEqual(baseline_client.list_calls, 1)
                self.assertCountEqual(baseline_client.details, TITLES)
                candidate_client = MixedBoardClient(provider.name)
                candidates = provider.fetch_candidates(target, candidate_client)
                self.assertEqual(candidate_client.list_calls, 1)
                self.assertCountEqual(candidate_client.details, ("3", "4", "5", "6"))
                self.assertEqual(
                    [job.source_job_id for job in candidates],
                    [
                        job.source_job_id
                        for job in baseline
                        if job.source_job_id in {"3", "4", "5", "6"}
                    ],
                )

    def test_plain_scan_does_not_build_duplicate_audit(self):
        class Provider:
            def validate_target(self, target):
                return None

            def fetch(self, target, client):
                return [make_job()]

        with (
            mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()),
            mock.patch(
                "swe_scraper.scanner.deduplicate_with_audit", side_effect=AssertionError
            ),
        ):
            result = scan_targets(
                [Target("example", "Example", "example")], client=object()
            )
        self.assertEqual(len(result.jobs), 1)

    def test_scanner_uses_candidate_fetch_only_for_filtered_scans(self):
        class Provider:
            def __init__(self):
                self.full_calls = 0
                self.candidate_calls = 0

            def validate_target(self, target):
                return None

            def fetch(self, target, client):
                self.full_calls += 1
                return [make_job()]

            def fetch_candidates(self, target, client):
                self.candidate_calls += 1
                return [make_job()]

        provider = Provider()
        target = Target("example", "Example", "example")
        with mock.patch("swe_scraper.scanner.get_provider", return_value=provider):
            self.assertEqual(len(scan_targets([target], client=object()).jobs), 1)
            self.assertEqual(
                len(scan_targets([target], client=object(), filter_swe=False).jobs), 1
            )
        self.assertEqual((provider.candidate_calls, provider.full_calls), (1, 1))


class CheckpointTests(unittest.TestCase):
    def test_resume_only_reuses_successful_boards(self):
        targets = [Target("example", "One", "one"), Target("example", "Two", "two")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kwargs = {
                "output": root / "jobs.csv",
                "targets": targets,
                "scan_options": {"filter_swe": True},
                "version": "test-version",
                "cache_root": root / "cache",
            }
            first = ScanCheckpointStore(**kwargs, resume=False)
            first.save(0, targets[0], [make_job()])
            resumed = ScanCheckpointStore(**kwargs, resume=True)
            self.assertEqual(resumed.load(0, targets[0]), [make_job()])
            self.assertIsNone(resumed.load(1, targets[1]))
            with self.assertRaisesRegex(ValueError, "does not match"):
                ScanCheckpointStore(
                    **{**kwargs, "scan_options": {"filter_swe": False}}, resume=True
                )
            first.clear()
            self.assertFalse(first.path.exists())

    def test_corrupt_or_old_board_is_retried(self):
        target = Target("example", "One", "one")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kwargs = {
                "output": root / "jobs.json",
                "targets": [target],
                "scan_options": {"filter_swe": True},
                "version": "test-version",
                "cache_root": root / "cache",
            }
            store = ScanCheckpointStore(**kwargs, resume=False)
            store.save(0, target, [make_job()])
            board = json.loads((store.path / "0.json").read_text(encoding="utf-8"))
            board["jobs"][0]["title"] = "tampered"
            (store.path / "0.json").write_text(json.dumps(board), encoding="utf-8")
            self.assertIsNone(ScanCheckpointStore(**kwargs, resume=True).load(0, target))
            (store.path / "0.json").write_text("{broken", encoding="utf-8")
            resumed = ScanCheckpointStore(**kwargs, resume=True)
            self.assertIsNone(resumed.load(0, target))
            manifest = json.loads((store.path / "manifest.json").read_text())
            manifest["created_at"] = "2000-01-01T00:00:00+00:00"
            (store.path / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "older than 24 hours"):
                ScanCheckpointStore(**kwargs, resume=True)

    def test_cli_partial_run_resumes_only_failed_board_and_cleans_success(self):
        class Provider:
            def __init__(self):
                self.calls = []
                self.fail_second = True

            def validate_target(self, target):
                return None

            def fetch(self, target, client):
                self.calls.append(target.slug)
                if target.slug == "two" and self.fail_second:
                    raise RuntimeError("temporary board failure")
                return [make_job(target.slug)]

        targets = [Target("example", "One", "one"), Target("example", "Two", "two")]
        provider = Provider()
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch("swe_scraper.cli.load_targets", return_value=targets),
            mock.patch("swe_scraper.scanner.get_provider", return_value=provider),
        ):
            root = Path(directory)
            cache = root / "cache"
            output = root / "jobs.json"
            with mock.patch(
                "swe_scraper.checkpoints._default_cache_root", return_value=cache
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        cli.main(
                            [
                                "scan",
                                "--target-set",
                                "all",
                                "--strict",
                                "--output",
                                str(output),
                            ]
                        ),
                        1,
                    )
                self.assertIn("Scan 2/2 boards", stderr.getvalue())
                self.assertEqual(len(json.loads(output.read_text())["jobs"]), 1)
                self.assertEqual(provider.calls.count("one"), 1)
                self.assertTrue(any(cache.iterdir()))

                provider.fail_second = False
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        cli.main(
                            [
                                "scan",
                                "--target-set",
                                "all",
                                "--resume",
                                "--output",
                                str(output),
                            ]
                        ),
                        0,
                    )
                self.assertEqual(provider.calls.count("one"), 1)
                self.assertEqual(provider.calls.count("two"), 2)
                self.assertEqual(len(json.loads(output.read_text())["jobs"]), 2)
                self.assertFalse(any(cache.iterdir()))

    def test_no_progress_hides_status_and_resume_requires_full_scan(self):
        class Provider:
            def validate_target(self, target):
                return None

            def fetch(self, target, client):
                return [make_job()]

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch(
                "swe_scraper.cli.load_targets",
                return_value=[Target("example", "Example", "example")],
            ),
            mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()),
        ):
            root = Path(directory)
            with mock.patch(
                "swe_scraper.checkpoints._default_cache_root", return_value=root / "cache"
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        cli.main(
                            [
                                "scan",
                                "--target-set",
                                "all",
                                "--no-progress",
                                "--output",
                                str(root / "out.json"),
                            ]
                        ),
                        0,
                    )
                self.assertNotIn("Scan 1/1 boards", stderr.getvalue())
                with (
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli.main(["scan", "--resume", "--output", str(root / "other.json")])
                self.assertEqual(raised.exception.code, 2)

    def test_failed_export_retains_checkpoint(self):
        class Provider:
            def validate_target(self, target):
                return None

            def fetch(self, target, client):
                return [make_job()]

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch(
                "swe_scraper.cli.load_targets",
                return_value=[Target("example", "Example", "example")],
            ),
            mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()),
            mock.patch("swe_scraper.cli._write_result", side_effect=OSError("disk full")),
        ):
            root = Path(directory)
            cache = root / "cache"
            with mock.patch(
                "swe_scraper.checkpoints._default_cache_root", return_value=cache
            ):
                with (
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli.main(
                        ["scan", "--target-set", "all", "--output", str(root / "out.json")]
                    )
                self.assertEqual(raised.exception.code, 2)
                self.assertTrue(any(cache.iterdir()))


if __name__ == "__main__":
    unittest.main()
