"""Validated recovery, pagination completeness, and deadline boundaries."""

import copy
import threading
import unittest
from concurrent.futures import Future
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.execution import (
    BoardDeadlineExceeded,
    FetchControl,
    ScanCancelled,
    fetch_context,
)
from swe_scraper.health import run_health_checks
from swe_scraper.models import Job
from swe_scraper.providers._reliability import (
    listing_snapshot,
    ordered_details,
    parse_listing_rows,
)
from swe_scraper.providers.base import Target
from swe_scraper.providers.results import BoardFetchResult, FetchIssue, PartialFetchError
from swe_scraper.providers.workday import WorkdayProvider
from swe_scraper.scanner import scan_targets


def job(identity):
    return Job(
        id=f"example:{identity}",
        company="Example",
        title="Software Engineer Intern",
        provider="example",
        source_job_id=str(identity),
        application_url=f"https://example.test/jobs/{identity}",
    )


def target(**options):
    return Target(
        "workday",
        "Example",
        "careers",
        {"origin": "https://example.test", "tenant": "example", **options},
    )


def rows(start, count):
    return [
        {
            "title": "Software Engineer Intern",
            "jobReqId": str(index),
            "externalPath": f"/job/NY/Intern_{index}",
            "locationsText": "New York",
        }
        for index in range(start, start + count)
    ]


class PageClient:
    def __init__(self, total=60, bad=False, fault=None):
        self.total = total
        self.bad = bad
        self.fault = fault
        self.offsets = []
        self.details = []

    def post_json(self, url, payload, **kwargs):
        offset = payload["offset"]
        self.offsets.append(offset)
        if offset == 20 and self.fault is not None:
            if isinstance(self.fault, Exception):
                raise self.fault
            return copy.deepcopy(self.fault)
        page_rows = rows(offset, min(20, max(0, self.total - offset)))
        if self.bad and offset == 20:
            page_rows[4] = {"jobReqId": "24"}
        return {"total": self.total, "jobPostings": page_rows}

    def get_json(self, url, **kwargs):
        self.details.append(url)
        return {"jobPostingInfo": {"jobDescription": "", "location": "New York"}}


class WorkdayRecoveryTests(unittest.TestCase):
    def test_bad_middle_row_retains_later_pages_without_false_completeness(self):
        client = PageClient(bad=True)
        with (
            fetch_context(FetchControl(threading.Event(), allow_partial=True)),
            self.assertRaises(PartialFetchError) as caught,
        ):
            WorkdayProvider().fetch(target(), client)
        result = caught.exception.result
        self.assertEqual(len(result.jobs), 59)
        self.assertEqual(client.offsets, [0, 20, 20, 40])
        self.assertTrue(result.pagination_complete)
        self.assertEqual(result.issues[0].stage, "record")
        self.assertIn("offset 20", result.issues[0].error)
        self.assertEqual(
            {j.source_job_id for j in result.jobs}, {str(i) for i in range(60)} - {"24"}
        )
        self.assertTrue(
            all(
                j.application_url.startswith("https://example.test/careers/job/")
                for j in result.jobs
            )
        )

    def test_strict_bad_row_does_not_request_later_pages(self):
        client = PageClient(bad=True)
        result = scan_targets([target()], client=client)
        self.assertFalse(result.jobs)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(client.offsets, [0, 20, 20])

    def test_healthy_empty_and_short_final_pages_remain_successful(self):
        for total in (0, 5, 20, 25, 40):
            with self.subTest(total=total):
                client = PageClient(total=total)
                result = scan_targets([target()], client=client, allow_partial=True)
                self.assertEqual(len(result.jobs), total)
                self.assertFalse(result.errors)

    def test_faulty_pagination_stops_and_keeps_only_previous_valid_pages(self):
        for fault in (
            {},
            {"total": True, "jobPostings": []},
            {"total": 61, "jobPostings": rows(20, 20)},
            {"total": 60, "jobPostings": rows(0, 20)},
            {"total": 60, "jobPostings": rows(20, 21)},
            {"total": 60, "jobPostings": []},
        ):
            with self.subTest(fault=fault):
                client = PageClient(fault=fault)
                with (
                    fetch_context(FetchControl(threading.Event(), allow_partial=True)),
                    self.assertRaises(PartialFetchError) as caught,
                ):
                    WorkdayProvider().fetch(target(), client)
                self.assertEqual(len(caught.exception.result.jobs), 20)
                self.assertFalse(caught.exception.result.pagination_complete)
                self.assertNotIn(40, client.offsets)

    def test_listing_deadline_retains_validated_prefix(self):
        result = scan_targets(
            [target()],
            client=PageClient(fault=BoardDeadlineExceeded("deadline")),
            allow_partial=True,
        )
        self.assertEqual(len(result.jobs), 20)
        self.assertTrue(all(j.metadata["board_complete"] is False for j in result.jobs))
        self.assertEqual(result.errors[0].details[0]["stage"], "deadline")

    def test_page_ceiling_keeps_prefix_and_reports_incomplete_pagination(self):
        with (
            fetch_context(FetchControl(threading.Event(), allow_partial=True)),
            self.assertRaises(PartialFetchError) as caught,
        ):
            WorkdayProvider().fetch(target(max_pages=1), PageClient())
        self.assertEqual(len(caught.exception.result.jobs), 20)
        self.assertFalse(caught.exception.result.pagination_complete)

    def test_empty_but_valid_details_pass_without_cache_provenance(self):
        result = scan_targets(
            [target()],
            client=PageClient(total=5),
            allow_partial=True,
            exclude_keywords=["crypto"],
            locations=["New York"],
        )
        self.assertEqual(len(result.jobs), 5)
        self.assertFalse(result.errors)
        self.assertTrue(all(j.description == "" for j in result.jobs))
        self.assertTrue(all("detail_fetched_at" not in j.metadata for j in result.jobs))

    def test_timed_out_details_never_fall_back_to_matching_summaries(self):
        class Client(PageClient):
            def get_json(self, url, **kwargs):
                raise BoardDeadlineExceeded("detail deadline")

        for options in (
            {"exclude_keywords": ["crypto"]},
            {"locations": ["New York"]},
            {"include_keywords": ["Example"]},
        ):
            for filter_swe in (True, False):
                with self.subTest(options=options, filter_swe=filter_swe):
                    result = scan_targets(
                        [target()],
                        client=Client(total=5),
                        allow_partial=True,
                        filter_swe=filter_swe,
                        **options,
                    )
                    self.assertFalse(result.jobs)
                    self.assertEqual(len(result.errors), 1)
                    self.assertEqual(result.errors[0].details[0]["stage"], "deadline")


class DeadlineBoundaryTests(unittest.TestCase):
    def test_health_rejects_typed_incomplete_boards_even_with_provider_quorum(self):
        for pagination_complete, stage in ((False, "pagination"), (True, "record")):

            class Provider:
                def validate_target(self, target):
                    pass

                def fetch(
                    self,
                    target,
                    client,
                    stage=stage,
                    pagination_complete=pagination_complete,
                ):
                    if target.slug == "bad":
                        raise PartialFetchError(
                            BoardFetchResult(
                                (job("partial"),),
                                (FetchIssue(stage, "partial", "invalid row"),),
                                pagination_complete,
                            )
                        )
                    return []

            with mock.patch("swe_scraper.health.get_provider", return_value=Provider()):
                report = run_health_checks(
                    [Target("example", slug, slug) for slug in ("first", "second", "bad")],
                    client=object(),
                )
            self.assertEqual(report.exit_code, 2)
            bad = next(check for check in report.checks if check.target == "bad")
            self.assertEqual(bad.pagination_complete, pagination_complete)

    def test_raw_listing_deadline_preserves_summary_type_and_pagination_flag(self):
        def fetch(retained):
            retained.append({"id": "summary"})
            raise BoardDeadlineExceeded("listing deadline")

        with fetch_context(FetchControl(threading.Event(), allow_partial=True)):
            summaries, issues, complete = listing_snapshot(fetch)
        self.assertEqual(summaries, [{"id": "summary"}])
        self.assertEqual(issues[0].stage, "deadline")
        self.assertFalse(complete)

    def test_single_response_parsing_deadline_retains_jobs_with_complete_listing(self):
        def parse(value):
            if value == 2:
                raise BoardDeadlineExceeded("parsing deadline")
            return [job(value)]

        with (
            fetch_context(FetchControl(threading.Event(), allow_partial=True)),
            self.assertRaises(PartialFetchError) as caught,
        ):
            parse_listing_rows([1, 2, 3], parse)
        self.assertEqual([j.source_job_id for j in caught.exception.result.jobs], ["1"])
        self.assertTrue(caught.exception.result.pagination_complete)

    def test_serial_details_preserve_successes_and_listing_completeness(self):
        def fetch(value):
            if value == 2:
                raise BoardDeadlineExceeded("detail deadline")
            return job(value)

        with (
            fetch_context(FetchControl(threading.Event(), allow_partial=True)),
            self.assertRaises(PartialFetchError) as caught,
        ):
            ordered_details(fetch, [1, 2, 3], 1)
        self.assertEqual([j.source_job_id for j in caught.exception.result.jobs], ["1"])
        self.assertTrue(caught.exception.result.pagination_complete)

    def test_detail_failure_retains_listing_flag_and_successes(self):
        def fetch(value):
            if value == 2:
                raise ValueError("invalid details")
            return job(value)

        with (
            fetch_context(FetchControl(threading.Event(), allow_partial=True)),
            self.assertRaises(PartialFetchError) as caught,
        ):
            ordered_details(fetch, [1, 2, 3], 1, pagination_complete=False)
        self.assertEqual(
            [j.source_job_id for j in caught.exception.result.jobs], ["1", "3"]
        )
        self.assertFalse(caught.exception.result.pagination_complete)

    def test_cancellation_is_never_converted_to_partial_recovery(self):
        stopped = threading.Event()

        def fetch(value):
            stopped.set()
            raise BoardDeadlineExceeded("deadline")

        with (
            fetch_context(FetchControl(stopped, allow_partial=True)),
            self.assertRaises(ScanCancelled),
        ):
            ordered_details(fetch, [1], 1)

    def test_strict_detail_deadline_propagates(self):
        with (
            fetch_context(FetchControl(threading.Event())),
            self.assertRaises(BoardDeadlineExceeded),
        ):
            ordered_details(
                lambda value: (_ for _ in ()).throw(BoardDeadlineExceeded("deadline")),
                [1],
                1,
            )

    def test_concurrent_deadline_harvests_done_successes_before_control_check(self):
        instant = {"value": 0.0}
        submitted = []

        class Executor:
            def __init__(self, **kwargs):
                self.futures = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.shutdown()

            def submit(self, function, *args):
                value = args[-1]
                submitted.append(value)
                future = Future()
                if value != 1:
                    try:
                        future.set_result(function(*args))
                    except Exception as exc:
                        future.set_exception(exc)
                self.futures.append(future)
                return future

            def shutdown(self, **kwargs):
                for future in self.futures:
                    if not future.done():
                        future.cancel()

        def wait(futures, **kwargs):
            instant["value"] = 2.0
            done = {future for future in futures if future.done()}
            return done, set(futures) - done

        with (
            fetch_context(FetchControl(threading.Event(), deadline=1, allow_partial=True)),
            mock.patch(
                "swe_scraper.execution.time.monotonic", side_effect=lambda: instant["value"]
            ),
            mock.patch("swe_scraper.providers._reliability.ThreadPoolExecutor", Executor),
            mock.patch("swe_scraper.providers._reliability.wait", side_effect=wait),
            self.assertRaises(PartialFetchError) as caught,
        ):
            ordered_details(job, [0, 1, 2, 3], 3)
        self.assertEqual(
            [j.source_job_id for j in caught.exception.result.jobs], ["0", "2"]
        )
        self.assertEqual(submitted, [0, 1, 2])
        self.assertTrue(caught.exception.result.pagination_complete)

    def test_scanner_retains_a_returned_board_after_deadline_only_in_recovery(self):
        for partial in (False, True):
            instant = {"value": 0.0}

            class Provider:
                def fetch(self, target, client, instant=instant):
                    instant["value"] = 2.0
                    return [job("ready")]

            with (
                mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()),
                mock.patch(
                    "swe_scraper.scanner.monotonic",
                    side_effect=lambda instant=instant: instant["value"],
                ),
                mock.patch(
                    "swe_scraper.execution.time.monotonic",
                    side_effect=lambda instant=instant: instant["value"],
                ),
            ):
                result = scan_targets(
                    [Target("example", "Example", "example")],
                    client=object(),
                    allow_partial=partial,
                    board_timeout=1,
                )
            self.assertEqual(len(result.jobs), int(partial))
            self.assertEqual(len(result.errors), 1)
            if partial:
                self.assertFalse(result.jobs[0].metadata["board_complete"])


if __name__ == "__main__":
    unittest.main()
