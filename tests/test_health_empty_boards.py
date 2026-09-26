"""Offline health regressions for empty boards and explicit count expectations."""

import unittest
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.health import HealthStatus, run_health_checks
from swe_scraper.models import Job
from swe_scraper.providers.base import HttpClient, Provider, Target
from swe_scraper.providers.workday import WorkdayProvider


def make_job(source_id):
    return Job(
        id=f"example:{source_id}",
        company="Example",
        title="Software Engineer Intern",
        application_url=f"https://example.test/jobs/{source_id}",
        provider="example",
        source_job_id=source_id,
    )


class EmptyBoardHealthTests(unittest.TestCase):
    def run_checks(self, targets, results, *, quorum=2):
        provider = mock.Mock(spec=Provider)
        client = mock.Mock(spec=HttpClient)

        def fetch(target, http):
            self.assertIs(http, client)
            result = results[target.slug]
            if isinstance(result, Exception):
                raise result
            return result

        provider.fetch.side_effect = fetch
        with mock.patch("swe_scraper.health.get_provider", return_value=provider):
            report = run_health_checks(targets, client=client, quorum=quorum)
        self.assertEqual(client.mock_calls, [])
        return report, provider

    def test_empty_only_quorum_is_healthy_with_exit_zero(self):
        for options in ({}, {"min_jobs": 0}):
            with self.subTest(options=options):
                targets = [
                    Target("example", "a", "a", options),
                    Target("example", "b", "b", options),
                ]
                report, provider = self.run_checks(targets, {"a": [], "b": []})
                self.assertEqual(provider.fetch.call_count, 2)
                self.assertEqual(report.status, HealthStatus.HEALTHY)
                self.assertEqual(report.exit_code, 0)
                self.assertEqual(
                    report.provider_statuses, {"example": HealthStatus.HEALTHY}
                )
                for check in report.to_dict()["checks"]:
                    self.assertEqual(check["status"], "healthy")
                    self.assertEqual(check["job_count"], 0)
                    self.assertEqual(check["category"], "empty")
                    self.assertEqual(check["error"], "")
                    self.assertTrue(check["pagination_complete"])

    def test_empty_boards_still_require_quorum_for_each_provider(self):
        targets = [Target("one", "a", "a"), Target("two", "b", "b")]
        report, _ = self.run_checks(targets, {"a": [], "b": []})
        self.assertTrue(all(c.status is HealthStatus.HEALTHY for c in report.checks))
        self.assertEqual(
            report.provider_statuses,
            {"one": HealthStatus.UNHEALTHY, "two": HealthStatus.UNHEALTHY},
        )
        self.assertEqual(report.exit_code, 2)

    def test_minimum_count_boundary_and_above_are_healthy(self):
        jobs = [make_job("1"), make_job("2")]
        for minimum in (0, 1, 2):
            with self.subTest(minimum=minimum):
                target = Target.from_mapping(
                    "example", {"name": "a", "slug": "a", "min_jobs": minimum}
                )
                report, _ = self.run_checks([target], {"a": jobs}, quorum=1)
                self.assertEqual(report.exit_code, 0)
                check = report.checks[0]
                self.assertEqual(check.job_count, 2)
                self.assertEqual(check.category, "")
                self.assertEqual(check.error, "")
                self.assertTrue(check.pagination_complete)

    def test_below_expected_count_respects_quorum_one_and_two(self):
        for jobs in ([], [make_job("1")]):
            for quorum, status, exit_code in (
                (1, HealthStatus.DEGRADED, 1),
                (2, HealthStatus.UNHEALTHY, 2),
            ):
                with self.subTest(job_count=len(jobs), quorum=quorum):
                    targets = [
                        Target("example", "a", "a"),
                        Target("example", "b", "b", {"min_jobs": 2}),
                    ]
                    report, _ = self.run_checks(
                        targets, {"a": [], "b": jobs}, quorum=quorum
                    )
                    self.assertEqual(report.status, status)
                    self.assertEqual(report.exit_code, exit_code)
                    self.assertEqual(report.provider_statuses, {"example": status})
                    check = report.checks[1]
                    self.assertEqual(check.status, HealthStatus.UNHEALTHY)
                    self.assertEqual(check.job_count, len(jobs))
                    self.assertEqual(check.category, "below_expected_count")
                    self.assertEqual(
                        check.error,
                        f"provider returned {len(jobs)} jobs; expected at least 2",
                    )
                    self.assertTrue(check.pagination_complete)

    def test_invalid_minimum_is_rejected_before_provider_fetch(self):
        for minimum in (-1, True, False, 0.0, 1.5, "0", "1", "", None, [], {}):
            with self.subTest(minimum=minimum):
                target = Target("example", "a", "a", {"min_jobs": minimum})
                report, provider = self.run_checks([target], {"a": []}, quorum=1)
                provider.validate_target.assert_not_called()
                provider.fetch.assert_not_called()
                self.assertEqual(report.exit_code, 2)
                check = report.checks[0]
                self.assertEqual(check.status, HealthStatus.UNHEALTHY)
                self.assertEqual(check.category, "contract")
                self.assertIn("min_jobs must be a nonnegative integer", check.error)

    def test_missing_or_malformed_provider_results_are_contract_failures(self):
        for result in (None, False, 0, "", "bad", {}, (), {"jobs": []}, [None], [{}]):
            with self.subTest(result=result):
                targets = [Target("example", "a", "a"), Target("example", "b", "b")]
                report, _ = self.run_checks(targets, {"a": [], "b": result}, quorum=1)
                self.assertEqual(report.exit_code, 2)
                check = report.checks[1]
                self.assertEqual(check.status, HealthStatus.UNHEALTHY)
                self.assertEqual(check.category, "contract")
                self.assertIn("provider must return a list of Job objects", check.error)

    def test_provider_errors_remain_failures_with_existing_quorum_rules(self):
        cases = (
            (RuntimeError("boom"), "provider", HealthStatus.DEGRADED),
            (TimeoutError("timeout"), "timeout", HealthStatus.DEGRADED),
            (RuntimeError("HTTP status 503"), "http", HealthStatus.DEGRADED),
            (ValueError("missing jobs payload"), "contract", HealthStatus.UNHEALTHY),
            (RuntimeError("pagination incomplete"), "pagination", HealthStatus.UNHEALTHY),
        )
        targets = [Target("example", "a", "a"), Target("example", "b", "b")]
        for error, category, status in cases:
            with self.subTest(category=category):
                report, _ = self.run_checks(targets, {"a": [], "b": error}, quorum=1)
                self.assertEqual(report.status, status)
                self.assertNotEqual(report.exit_code, 0)
                check = report.checks[1]
                self.assertEqual(check.status, HealthStatus.UNHEALTHY)
                self.assertEqual(check.job_count, 0)
                self.assertEqual(check.category, category)
                self.assertEqual(check.error, f"{type(error).__name__}: {error}")
                self.assertEqual(check.pagination_complete, category != "pagination")

    def test_invalid_provider_target_still_fails_before_fetch(self):
        provider = mock.Mock(spec=Provider)
        provider.validate_target.side_effect = ValueError("bad target")
        client = mock.Mock(spec=HttpClient)
        with mock.patch("swe_scraper.health.get_provider", return_value=provider):
            report = run_health_checks([Target("example", "a", "a")], client=client)
        provider.fetch.assert_not_called()
        self.assertEqual(client.mock_calls, [])
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.checks[0].category, "contract")

    def test_workday_empty_payload_succeeds_but_invalid_payloads_still_fail(self):
        target = Target(
            "workday",
            "Example",
            "careers",
            {"origin": "https://example.test", "tenant": "example"},
        )
        cases = (
            ({"total": 0, "jobPostings": []}, HealthStatus.HEALTHY, "empty", True),
            (None, HealthStatus.UNHEALTHY, "provider", True),
            ({}, HealthStatus.UNHEALTHY, "pagination", False),
            ({"total": 0}, HealthStatus.UNHEALTHY, "pagination", False),
            (
                {"total": 0, "jobPostings": None},
                HealthStatus.UNHEALTHY,
                "pagination",
                False,
            ),
            ({"total": 1, "jobPostings": []}, HealthStatus.UNHEALTHY, "pagination", False),
        )
        for payload, status, category, complete in cases:
            with self.subTest(payload=payload):
                client = mock.Mock(spec=HttpClient)
                client.post_json.return_value = payload
                with mock.patch(
                    "swe_scraper.health.get_provider", return_value=WorkdayProvider()
                ):
                    report = run_health_checks([target], client=client, quorum=1)
                client.post_json.assert_called_once()
                check = report.checks[0]
                self.assertEqual(check.status, status)
                self.assertEqual(check.category, category)
                self.assertEqual(check.pagination_complete, complete)
                self.assertEqual(
                    report.exit_code, 0 if status is HealthStatus.HEALTHY else 2
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
