"""Behavioral regressions found during the scraper review."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
from _bootstrap import ROOT
from requests.adapters import BaseAdapter

from swe_scraper import cli
from swe_scraper.filters import filter_jobs, is_swe_internship
from swe_scraper.models import Job, ProviderFailure, ScanResult
from swe_scraper.providers.base import Target
from swe_scraper.providers.http import RequestsJsonClient
from swe_scraper.providers.workday import WorkdayProvider


def sample_job(title="Software Engineer Intern"):
    return Job(
        id="example:1",
        company="Example",
        title=title,
        application_url="https://example.test/jobs/1",
        provider="example",
        source_job_id="1",
        locations=("New York, NY",),
    )


class WorkdayCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.target = Target(
            "workday",
            "Example",
            "careers",
            {
                "origin": "https://example.myworkdayjobs.com",
                "tenant": "example",
                "page_size": 2,
                "max_pages": 3,
            },
        )
        self.pages = json.loads(
            (ROOT / "tests/fixtures/providers/workday-pagination.json").read_text()
        )

    def fetch(self, pages):
        client = mock.Mock()
        client.post_json.side_effect = pages
        return WorkdayProvider().fetch(self.target, client), client

    def test_complete_multiple_pages_and_empty_board(self):
        jobs, client = self.fetch(self.pages["complete"])
        self.assertEqual([job.source_job_id for job in jobs], ["R1", "R2", "R3"])
        self.assertEqual(
            [call.args[1]["offset"] for call in client.post_json.call_args_list], [0, 2]
        )
        jobs, _ = self.fetch([{"total": 0, "jobPostings": []}])
        self.assertEqual(jobs, [])

    def test_short_page_with_more_jobs_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete pagination"):
            self.fetch(self.pages["incomplete"])

    def test_later_pages_can_report_zero_total_without_ending_pagination(self):
        first, second = self.pages["complete"]
        jobs, client = self.fetch([first, {**second, "total": 0}])
        self.assertEqual(len(jobs), 3)
        self.assertEqual(client.post_json.call_count, 2)

    def test_zero_total_does_not_hide_a_missing_final_page(self):
        first, _ = self.pages["complete"]
        with self.assertRaisesRegex(RuntimeError, "incomplete pagination"):
            self.fetch([first, {"total": 0, "jobPostings": []}])

    def test_default_limit_supports_boards_with_more_than_five_pages(self):
        target = Target(
            "workday",
            "Example",
            "careers",
            {
                "origin": "https://example.myworkdayjobs.com",
                "tenant": "example",
            },
        )
        pages = [
            {
                "total": 101 if page == 0 else 0,
                "jobPostings": [
                    {"title": "Software Intern", "externalPath": f"/job/Role_R{i}"}
                    for i in range(page * 20, min((page + 1) * 20, 101))
                ],
            }
            for page in range(6)
        ]
        client = mock.Mock()
        client.post_json.side_effect = pages
        self.assertEqual(len(WorkdayProvider().fetch(target, client)), 101)

    def test_missing_or_malformed_contract_fails_closed(self):
        for page in self.pages["malformed"]:
            with self.subTest(page=page), self.assertRaises(RuntimeError):
                self.fetch([page])

    def test_changed_totals_and_repeated_rows_fail_closed(self):
        first, second = self.pages["complete"]
        cases = [
            [first, {**second, "total": 4}],
            [first, {**second, "jobPostings": first["jobPostings"][:1]}],
            [{**first, "total": 1}],
        ]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(RuntimeError):
                self.fetch(pages)


class CountingBody(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.bytes_read = 0

    def read(self, *args, **kwargs):
        value = super().read(*args, **kwargs)
        self.bytes_read += len(value)
        return value

    def release_conn(self):
        # Requests returns an exhausted urllib3 connection to its pool here.
        self.close()


class MemoryAdapter(BaseAdapter):
    """Exercise real Requests streaming without making network requests."""

    def __init__(self, body, status=200, content_type="application/json"):
        self.body, self.status, self.content_type = body, status, content_type

    def send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = self.status
        response.url = request.url
        response.headers["Content-Type"] = self.content_type
        response.raw = self.body
        response.request = request
        response.encoding = requests.utils.get_encoding_from_headers(response.headers)
        return response

    def close(self):
        self.body.close()


class StreamingLimitTests(unittest.TestCase):
    def request(self, method, payload, limit, status=200, content_type="application/json"):
        body = CountingBody(payload)
        session = requests.Session()
        session.mount("https://example.test/", MemoryAdapter(body, status, content_type))
        client = RequestsJsonClient(min_host_interval=0, max_response_bytes=limit)
        self.addCleanup(session.close)
        with mock.patch.object(client, "_session", return_value=session):
            args = (
                ("https://example.test/data", {})
                if method == "post_json"
                else ("https://example.test/data",)
            )
            try:
                return getattr(client, method)(*args), body
            except Exception:
                self.assertTrue(body.closed, "failed requests must release the connection")
                self.assertLessEqual(body.bytes_read, limit + 1)
                raise

    def test_large_bodies_stop_reading_at_the_limit(self):
        payload = json.dumps({"data": "x" * (2 * 1024 * 1024)}).encode()
        for method in ("get_json", "post_json", "get_text"):
            with (
                self.subTest(method=method),
                self.assertRaisesRegex(ValueError, "exceeded"),
            ):
                self.request(method, payload, 1024)

    def test_success_at_exact_limit_and_response_cleanup(self):
        for method in ("get_json", "post_json", "get_text"):
            with self.subTest(method=method):
                value, body = self.request(method, b'{"ok":true}', 11)
                self.assertEqual(
                    value, '{"ok":true}' if method == "get_text" else {"ok": True}
                )
                self.assertTrue(body.closed)

    def test_status_and_parse_failures_close_the_response(self):
        with self.assertRaises(requests.HTTPError):
            self.request("get_json", b"error", 1024, status=500)
        with self.assertRaises(ValueError):
            self.request("get_json", b"not json", 1024)

    def test_text_encoding_is_preserved(self):
        value, body = self.request(
            "get_text", b"caf\xe9", 4, content_type="text/html; charset=iso-8859-1"
        )
        self.assertEqual(value, "café")
        self.assertTrue(body.closed)


class RoleFilterTests(unittest.TestCase):
    def test_software_roles_remain_and_unrelated_roles_are_excluded(self):
        for title in (
            "Software Intern",
            "SWE Intern",
            "Backend Developer Intern",
            "Data Engineering Intern",
            "Machine Learning Engineer Intern",
            "Security Software Engineer Intern",
            "Firmware Intern",
        ):
            with self.subTest(title=title):
                self.assertTrue(is_swe_internship(title))
        for title in (
            "Physical Security Intern",
            "Data Analytics Intern",
            "Quantitative Research Intern",
            "Technology Operations Intern",
        ):
            with self.subTest(title=title):
                self.assertFalse(is_swe_internship(title))

    def test_adjacent_roles_are_opt_in_and_still_respect_filters(self):
        job = sample_job("Data Analytics Intern")
        self.assertEqual(filter_jobs([job]), [])
        self.assertEqual(filter_jobs([job], include_adjacent=True), [job])
        self.assertEqual(
            filter_jobs([job], include_adjacent=True, locations=["Boston"]), []
        )
        self.assertEqual(
            filter_jobs([sample_job("Physical Security Intern")], include_adjacent=True), []
        )


class StrictScanTests(unittest.TestCase):
    def test_strict_scan_and_watch_report_partial_failure_with_saved_output(self):
        errors = [ProviderFailure("workday", "Example", "example", "timeout")]
        cases = [([sample_job()], errors, 1), ([], errors, 2), ([], [], 0)]
        for command in ("scan", "watch"):
            for jobs, failures, expected in cases:
                with (
                    self.subTest(command=command, jobs=bool(jobs), errors=bool(failures)),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    args = [command, "--strict", "--output", str(root / "jobs.json")]
                    if command == "watch":
                        args += ["--once", "--state", str(root / "seen.json")]
                    result = ScanResult.from_iterables(jobs, failures)
                    with mock.patch("swe_scraper.cli._run_scan", return_value=result):
                        self.assertEqual(cli.main(args), expected)
                    saved = json.loads((root / "jobs.json").read_text())
                    self.assertEqual(len(saved["jobs"]), len(jobs))
                    self.assertEqual(len(saved["errors"]), len(failures))

    def test_strict_watch_stops_on_failure(self):
        result = ScanResult.from_iterables(
            [sample_job()], [ProviderFailure("workday", "Example", "example", "timeout")]
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch("swe_scraper.cli._run_scan", return_value=result),
            mock.patch("swe_scraper.cli.time.sleep") as sleep,
        ):
            root = Path(directory)
            self.assertEqual(
                cli.main(
                    [
                        "watch",
                        "--strict",
                        "--output",
                        str(root / "jobs.json"),
                        "--state",
                        str(root / "seen.json"),
                    ]
                ),
                1,
            )
            sleep.assert_not_called()

    def test_cli_threads_adjacent_option_to_scanner(self):
        args = cli.build_parser().parse_args(["scan", "--include-adjacent"])
        with (
            mock.patch("swe_scraper.cli.load_targets", return_value=[]),
            mock.patch(
                "swe_scraper.cli.scan_targets", return_value=ScanResult.from_iterables([])
            ) as scan,
        ):
            cli._run_scan(args)
        self.assertTrue(scan.call_args.kwargs["include_adjacent"])
