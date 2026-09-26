"""Regressions for the independently reproduced review findings."""

import io
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _bootstrap import ROOT

from swe_scraper import cli
from swe_scraper.config import load_targets
from swe_scraper.dedupe import deduplicate
from swe_scraper.models import Job, JobSource, ScanResult
from swe_scraper.normalize import iso_datetime
from swe_scraper.providers.base import Target
from swe_scraper.providers.workday import WorkdayProvider
from swe_scraper.scanner import scan_targets_detailed


def job(identifier, **overrides):
    values = dict(
        id=identifier,
        company="Example",
        title="Software Intern",
        provider="example",
        source_job_id=identifier,
        application_url=f"https://example.test/{identifier}",
        locations=("London",),
        description="Java",
    )
    return Job(**(values | overrides))


class FilterCompositionTests(unittest.TestCase):
    def test_all_jobs_keeps_explicit_constraints_and_non_internships(self):
        good = job(
            "good", title="Software Engineer", locations=("New York",), description="Python"
        )
        provider = mock.Mock()
        provider.fetch.return_value = [
            good,
            job("wrong-city", description="Python"),
            job("wrong-language", locations=("New York",)),
            job("excluded", locations=("New York",), description="Python Java"),
        ]
        with mock.patch("swe_scraper.scanner.get_provider", return_value=provider):
            result = scan_targets_detailed(
                [Target("example", "Example", "example")],
                client=mock.Mock(),
                filter_swe=False,
                locations=["New York"],
                include_keywords=["Python"],
                exclude_keywords=["Java"],
            )
        self.assertEqual(result.result.jobs, (good,))


class CompactDateTests(unittest.TestCase):
    def test_compact_dates_and_epochs_are_distinguished(self):
        for value in ("20250101", "2025-01-01", "1735689600", 1735689600, 1735689600000):
            with self.subTest(value=value):
                self.assertEqual(iso_datetime(value), "2025-01-01T00:00:00+00:00")
        self.assertEqual(iso_datetime("20240229"), "2024-02-29T00:00:00+00:00")
        self.assertEqual(iso_datetime(20250101), "1970-08-23T09:01:41+00:00")
        for value in ("20250229", "20251301", "20250100"):
            with self.subTest(value=value):
                self.assertEqual(iso_datetime(value), "")


class WorkdayOptionTests(unittest.TestCase):
    def test_empty_query_and_slash_wrapped_site(self):
        target = Target(
            "workday",
            "Example",
            "careers",
            {
                "origin": "https://example.test",
                "tenant": "example",
                "site": " /careers/ ",
                "search_text": "",
            },
        )
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [{"title": "Software Intern", "externalPath": "/job/Role_R1"}],
        }
        result = WorkdayProvider().fetch(target, client)
        self.assertEqual(client.post_json.call_args.args[1]["searchText"], "")
        self.assertEqual(
            client.post_json.call_args.args[0],
            "https://example.test/wday/cxs/example/careers/jobs",
        )
        self.assertEqual(
            result[0].application_url, "https://example.test/careers/job/Role_R1"
        )

    def test_invalid_site_is_rejected_before_network(self):
        for site in ("///", "careers/other", "careers?x=1"):
            client = mock.Mock()
            target = Target(
                "workday",
                "Example",
                "careers",
                {
                    "origin": "https://example.test",
                    "tenant": "example",
                    "site": site,
                },
            )
            with self.subTest(site=site), self.assertRaises(ValueError):
                WorkdayProvider().fetch(target, client)
            client.post_json.assert_not_called()


class ExactComponentTests(unittest.TestCase):
    def test_bridge_combines_existing_exact_groups_in_every_input_order(self):
        jobs = [
            job(
                "a",
                locations=("Austin",),
                source_job_id="alpha",
                application_url="https://example.test/1",
            ),
            job(
                "b",
                locations=("Boston",),
                source_job_id="beta",
                application_url="https://example.test/2",
            ),
            job(
                "c",
                locations=("Chicago",),
                source_job_id="beta",
                application_url="https://example.test/1",
            ),
        ]
        outputs = []
        for order in itertools.permutations(jobs):
            merged = deduplicate(order)
            self.assertEqual(len(merged), 1)
            self.assertEqual(len(merged[0].sources), 3)
            self.assertEqual(
                {item.reason for item in merged[0].merge_evidence},
                {"exact_url", "source_identity"},
            )
            outputs.append(merged[0].to_dict())
        self.assertTrue(all(value == outputs[0] for value in outputs))

    def test_existing_provenance_can_supply_the_bridge(self):
        first = job(
            "first",
            title="A Intern",
            sources=(
                JobSource("example", "first", "https://example.test/first"),
                JobSource("feed", "legacy", "https://example.test/legacy"),
            ),
        )
        second = job(
            "second",
            title="B Intern",
            sources=(
                JobSource("example", "second", "https://example.test/second"),
                JobSource("feed", "legacy", "https://example.test/moved"),
            ),
        )
        self.assertEqual(len(deduplicate([first, second])), 1)

    def test_semantic_location_chain_does_not_merge_disjoint_roles(self):
        jobs = [
            job("a", locations=("Austin",)),
            job("b", locations=("Austin", "Boston")),
            job("c", locations=("Boston",)),
        ]
        for order in itertools.permutations(jobs):
            self.assertEqual(len(deduplicate(order)), 2)

    def test_intern_alias_still_matches_but_full_time_does_not(self):
        self.assertEqual(
            len(
                deduplicate(
                    [
                        job("a", title="Software Engineering Internship"),
                        job("b", title="Software Engineer Intern"),
                    ]
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                deduplicate(
                    [
                        job("a", title="Software Engineer"),
                        job("b", title="Software Engineer Intern"),
                    ]
                )
            ),
            2,
        )


class ProfileSelectionTests(unittest.TestCase):
    def test_custom_defaults_and_explicit_canary_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(
                json.dumps(
                    {
                        "greenhouse": [
                            {"name": "Normal", "slug": "normal"},
                            {"name": "Canary", "slug": "canary", "profiles": ["canary"]},
                        ]
                    }
                )
            )
            self.assertEqual([x.slug for x in load_targets(path)], ["normal"])
            self.assertEqual(
                [x.slug for x in load_targets(path, profile="canary")], ["canary"]
            )
            self.assertEqual(len(load_targets(path, profile="all")), 2)

    def test_profile_miss_and_malformed_profiles_have_precise_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            for profiles in (["priority"], "canary", ["canry"]):
                path.write_text(
                    json.dumps(
                        {
                            "greenhouse": [
                                {"name": "Example", "slug": "example", "profiles": profiles}
                            ]
                        }
                    )
                )
                with (
                    self.subTest(profiles=profiles),
                    self.assertRaisesRegex(ValueError, "profile"),
                ):
                    load_targets(path, ["greenhouse"], profile="canary")

    def test_quick_with_custom_targets_fails_before_loading(self):
        args = cli.build_parser().parse_args(
            ["scan", "--quick", "--targets", "custom.json"]
        )
        with (
            mock.patch("swe_scraper.cli.load_targets") as load,
            self.assertRaisesRegex(ValueError, "--quick"),
        ):
            cli._run_scan(args)
        load.assert_not_called()


class ConsoleEncodingTests(unittest.TestCase):
    def test_watch_saves_unicode_jobs_and_state_on_legacy_consoles(self):
        for encoding in ("cp437", "cp1252", "utf-8"):
            with (
                self.subTest(encoding=encoding),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                stdout = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")
                self.addCleanup(stdout.close)
                result = ScanResult.from_iterables([job("one", company="Example 東京")])
                with (
                    mock.patch("swe_scraper.cli._run_scan", return_value=result),
                    mock.patch("sys.stdout", stdout),
                ):
                    code = cli.main(
                        [
                            "watch",
                            "--once",
                            "--output",
                            str(root / "jobs.json"),
                            "--state",
                            str(root / "seen.json"),
                        ]
                    )
                self.assertEqual(code, 0)
                self.assertTrue((root / "seen.json").exists())
                self.assertEqual(
                    json.loads((root / "jobs.json").read_text(encoding="utf-8"))["jobs"][0][
                        "company"
                    ],
                    "Example 東京",
                )


class CatalogAuditTests(unittest.TestCase):
    def test_catalog_origins_are_checked_even_when_fixtures_pass(self):
        with mock.patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
            import audit_public_repo
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "src/swe_scraper/data/targets.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "workday": [
                            {
                                "name": "Bad",
                                "slug": "bad",
                                "origin": "http://insecure.example.test",
                            }
                        ]
                    }
                )
            )
            fixtures = root / "tests/fixtures"
            fixtures.mkdir(parents=True)
            (fixtures / "good.json").write_text(
                json.dumps({"application_url": "https://example.test/job"})
            )
            count, failures = audit_public_repo.scan_urls(root)
        self.assertEqual(count, 2)
        self.assertEqual(failures, ["workday:Bad:origin"])

    def test_actual_catalog_origins_are_all_counted(self):
        with mock.patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
            import audit_public_repo
        catalog = json.loads(
            (ROOT / "src/swe_scraper/data/targets.json").read_text(encoding="utf-8")
        )
        expected = sum(
            bool(row.get(key))
            for rows in catalog.values()
            for row in rows
            for key in ("origin", "search_url")
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "src/swe_scraper/data/targets.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(catalog))
            count, failures = audit_public_repo.scan_urls(root)
        self.assertGreater(expected, 0)
        self.assertEqual(count, expected)
        self.assertEqual(failures, [])
