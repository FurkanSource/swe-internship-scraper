"""Regressions for cancellation precedence and strict listing health contracts."""

import re
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.execution import (
    BoardDeadlineExceeded,
    FetchControl,
    ScanCancelled,
    current_control,
    fetch_context,
)
from swe_scraper.health import HealthStatus, run_health_checks
from swe_scraper.models import Job
from swe_scraper.providers._reliability import ListingValidationError, ordered_details
from swe_scraper.providers.base import Target
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.results import BoardFetchResult, FetchIssue, PartialFetchError
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider
from swe_scraper.scanner import scan_targets


def job(identity):
    return Job(
        id=f"example:{identity}",
        company="Example",
        title="Software Engineer Intern",
        application_url=f"https://example.test/jobs/{identity}",
        provider="example",
        source_job_id=identity,
    )


def target(provider, slug="example"):
    return Target(
        provider,
        slug,
        slug,
        {"origin": f"https://{slug}.example.test", "page_size": 1},
    )


def oracle_page(rows, total, has_more):
    return {
        "hasMore": has_more,
        "items": [
            {
                "TotalJobsCount": total,
                "requisitionList": {"items": rows, "hasMore": has_more},
            }
        ],
    }


class OracleClient:
    def __init__(self, pages):
        self.pages = pages
        self.offsets = []
        self.details = []

    def get_json(self, url, **kwargs):
        finder = parse_qs(urlsplit(url).query)["finder"][0]
        if "findReqs;" in finder:
            offset = int(re.search(r"offset=(\d+)", finder).group(1))
            self.offsets.append(offset)
            return self.pages[offset]
        identity = re.search(r'Id="([^"]+)"', finder).group(1)
        self.details.append(identity)
        return {"items": [{"Id": identity, "Title": "Software Engineer Intern"}]}


class CancellationReviewTests(unittest.TestCase):
    def test_cancellation_during_detail_shutdown_overrides_deadline_recovery(self):
        stopped = threading.Event()
        inflight = threading.Event()
        release = threading.Event()
        shutdown_entered = threading.Event()

        class Executor(ThreadPoolExecutor):
            def shutdown(self, wait=True, *, cancel_futures=False):
                shutdown_entered.set()
                stopped.set()
                release.set()
                super().shutdown(wait=wait, cancel_futures=cancel_futures)

        def fetch(identity):
            if identity == "deadline":
                if not inflight.wait(5):
                    raise AssertionError("second detail worker did not start")
                raise BoardDeadlineExceeded("detail deadline")
            inflight.set()
            if not release.wait(5):
                raise AssertionError("executor shutdown did not release in-flight detail")
            return job(identity)

        with (
            fetch_context(FetchControl(stopped, allow_partial=True)),
            mock.patch("swe_scraper.providers._reliability.ThreadPoolExecutor", Executor),
            self.assertRaises(ScanCancelled),
        ):
            ordered_details(fetch, ["deadline", "inflight"], 2)
        self.assertTrue(shutdown_entered.is_set())

    def test_scanner_partial_exception_checks_cancellation_before_retaining_jobs(self):
        class Provider:
            def fetch(self, target, client):
                current_control().cancelled.set()
                raise PartialFetchError(
                    BoardFetchResult(
                        (job("unwanted"),), (FetchIssue("record", "bad", "bad row"),), True
                    )
                )

        checkpoint = mock.Mock()
        checkpoint.load.return_value = None
        with (
            mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()),
            self.assertRaises(ScanCancelled),
        ):
            scan_targets(
                [Target("example", "Example", "example")],
                client=object(),
                allow_partial=True,
                checkpoint_store=checkpoint,
            )
        checkpoint.save.assert_not_called()


class OraclePaginationReviewTests(unittest.TestCase):
    def test_known_total_continues_across_false_has_more_nested_pages(self):
        identifiers = [str(index) for index in range(29)]
        client = OracleClient(
            {
                0: oracle_page(
                    [{"Id": value, "Title": "Intern"} for value in identifiers[:25]],
                    29,
                    False,
                ),
                25: oracle_page(
                    [{"Id": value, "Title": "Intern"} for value in identifiers[25:]],
                    29,
                    False,
                ),
            }
        )
        board = Target(
            "oracle",
            "Oracle",
            "oracle",
            {"origin": "https://oracle.example.test", "page_size": 25},
        )
        jobs = OracleProvider().fetch(board, client)
        self.assertEqual(client.offsets, [0, 25])
        self.assertEqual(client.details, identifiers)
        self.assertEqual([j.source_job_id for j in jobs], identifiers)
        self.assertEqual(
            [j.application_url for j in jobs],
            [
                f"https://oracle.example.test/hcmUI/CandidateExperience/en/sites/oracle/job/{value}"
                for value in identifiers
            ],
        )

    def test_complete_short_and_empty_oracle_listings_remain_successful(self):
        for total in (0, 2):
            pages = {0: oracle_page([], 0, False)}
            if total:
                pages = {
                    0: oracle_page([{"Id": "first", "Title": "Intern"}], 2, True),
                    1: oracle_page([{"Id": "last", "Title": "Intern"}], 2, False),
                }
            client = OracleClient(pages)
            with self.subTest(total=total):
                self.assertEqual(
                    len(OracleProvider().fetch(target("oracle"), client)), total
                )
                self.assertEqual(client.offsets, [0, 1] if total else [0])


class StrictHealthReviewTests(unittest.TestCase):
    def test_smartrecruiters_listing_validation_raises_typed_at_source(self):
        provider = SmartRecruitersProvider()
        for payload in (
            {"unexpected": []},
            {"content": [], "totalFound": None},
            {"content": [{"name": "Intern"}], "totalFound": 1},
        ):
            client = mock.Mock()
            client.get_json.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(ListingValidationError):
                provider._fetch_posting_ids(
                    target("smartrecruiters"),
                    client,
                    "https://api.smartrecruiters.com/v1/companies/example/postings",
                )

    def check_invalid_row(self, provider, row, *, reported_total=None):
        class Client:
            def get_json(self, url, **kwargs):
                parts = urlsplit(url)
                if provider == "smartrecruiters":
                    slug = parts.path.split("/")[3]
                    if parts.path.endswith("/postings"):
                        rows = [row] if slug == "bad" else [{"id": slug, "name": "Intern"}]
                        total = (
                            reported_total
                            if slug == "bad" and reported_total is not None
                            else len(rows)
                        )
                        return {"totalFound": total, "content": rows}
                    return {"id": slug, "name": "Software Engineer Intern"}
                slug = parts.hostname.split(".")[0]
                if parts.path.endswith("/recruitingCEJobRequisitions"):
                    rows = [row] if slug == "bad" else [{"Id": slug, "Title": "Intern"}]
                    total = (
                        reported_total
                        if slug == "bad" and reported_total is not None
                        else len(rows)
                    )
                    return oracle_page(rows, total, False)
                return {"items": [{"Id": slug, "Title": "Software Engineer Intern"}]}

        report = run_health_checks(
            [target(provider, slug) for slug in ("first", "second", "bad")],
            client=Client(),
            quorum=2,
        )
        self.assertEqual(report.exit_code, 2)
        self.assertIs(report.provider_statuses[provider], HealthStatus.UNHEALTHY)
        bad = next(check for check in report.checks if check.target == "bad")
        self.assertIs(bad.status, HealthStatus.UNHEALTHY)
        self.assertFalse(bad.pagination_complete)
        self.assertIn(bad.category, {"contract", "pagination"})
        for healthy in (check for check in report.checks if check.target != "bad"):
            self.assertIs(healthy.status, HealthStatus.HEALTHY)
            self.assertTrue(healthy.pagination_complete)
            self.assertEqual(healthy.job_count, 1)

    def test_smartrecruiters_missing_id_cannot_be_softened_by_healthy_siblings(self):
        self.check_invalid_row("smartrecruiters", {"name": "Intern"})

    def test_smartrecruiters_malformed_row_reports_incomplete_pagination(self):
        self.check_invalid_row("smartrecruiters", None)

    def test_oracle_missing_id_cannot_be_softened_by_healthy_siblings(self):
        self.check_invalid_row("oracle", {"Title": "Intern"})

    def test_oracle_malformed_row_reports_incomplete_pagination(self):
        self.check_invalid_row("oracle", None)

    def test_total_overshoot_cannot_be_softened_by_healthy_siblings(self):
        for provider, row in (
            ("smartrecruiters", {"id": "bad", "name": "Intern"}),
            ("oracle", {"Id": "bad", "Title": "Intern"}),
        ):
            with self.subTest(provider=provider):
                self.check_invalid_row(provider, row, reported_total=0)


if __name__ == "__main__":
    unittest.main()
