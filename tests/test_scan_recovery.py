"""Coverage-preserving recovery and cooperative cancellation contracts."""

import concurrent.futures
import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper import cli
from swe_scraper.checkpoints import ScanCheckpointStore
from swe_scraper.health import run_health_checks
from swe_scraper.models import Job
from swe_scraper.providers.base import Target
from swe_scraper.scanner import scan_targets


def job(identity):
    return Job(
        id=f"example:{identity}",
        company="Example",
        title="Software Engineer Intern",
        provider="example",
        source_job_id=identity,
        application_url=f"https://example.test/jobs/{identity}",
    )


class WorkdayClient:
    def __init__(self):
        self.calls = 0

    def post_json(self, url, payload, **kwargs):
        self.calls += 1
        if payload["offset"] == 0:
            return {
                "total": 21,
                "jobPostings": [
                    {
                        "title": "Software Engineer Intern",
                        "jobReqId": str(index),
                        "externalPath": f"/job/NY/Intern_{index}",
                    }
                    for index in range(20)
                ],
            }
        return {"total": 21, "jobPostings": [{"jobReqId": "bad"}]}


class DetailClient:
    def get_json(self, url, **kwargs):
        if url.endswith("/postings"):
            return {
                "totalFound": 3,
                "content": [
                    {"id": identity, "name": "Software Engineer Intern"}
                    for identity in ("first", "bad", "last")
                ],
            }
        identity = url.rsplit("/", 1)[1]
        return (
            {}
            if identity == "bad"
            else {
                "id": identity,
                "name": "Software Engineer Intern",
                "location": {"city": "New York"},
            }
        )


class RecoveryTests(unittest.TestCase):
    def test_recovery_preserves_both_listing_and_detail_errors(self):
        row = {
            "id": "good",
            "title": "Software Intern",
            "absolute_url": "https://example.test/good",
        }
        client = mock.Mock()

        def response(url, **kwargs):
            if url.endswith("/jobs"):
                return {"jobs": [row, {**row, "id": "bad-detail"}, None]}
            return {} if url.endswith("bad-detail") else {**row, "content": "Python"}

        client.get_json.side_effect = response
        result = scan_targets(
            [Target("greenhouse", "Example", "example", {"listing_only": True})],
            client=client,
            allow_partial=True,
            include_keywords=["Python"],
        )
        self.assertEqual([j.source_job_id for j in result.jobs], ["good"])
        self.assertEqual(
            [i["stage"] for i in result.errors[0].details], ["record", "detail"]
        )

    def test_recovered_listings_still_fetch_details_before_filtering(self):
        for provider in ("workday", "greenhouse"):
            for filter_swe in (True, False):
                with self.subTest(provider=provider, filter_swe=filter_swe):
                    client = mock.Mock()
                    if provider == "workday":
                        target = Target(
                            provider,
                            "Example",
                            "careers",
                            {
                                "origin": "https://example.wd1.myworkdayjobs.com",
                                "tenant": "example",
                            },
                        )
                        client.post_json.return_value = {
                            "total": 2,
                            "jobPostings": [
                                {
                                    "title": "Software Intern",
                                    "externalPath": "/job/Intern_good",
                                },
                                {},
                            ],
                        }
                        client.get_json.return_value = {
                            "jobPostingInfo": {
                                "jobDescription": "Build distributed systems",
                                "location": "New York",
                            }
                        }
                    else:
                        target = Target(
                            provider, "Example", "example", {"listing_only": True}
                        )
                        row = {
                            "id": "good",
                            "title": "Software Intern",
                            "absolute_url": "https://example.test/good",
                        }
                        client.get_json.side_effect = [
                            {"jobs": [row, None]},
                            {
                                **row,
                                "content": "Build distributed systems",
                                "location": {"name": "New York"},
                            },
                        ]
                    result = scan_targets(
                        [target],
                        client=client,
                        allow_partial=True,
                        filter_swe=filter_swe,
                        locations=["New York"],
                        include_keywords=["distributed"],
                    )
                    self.assertEqual(len(result.jobs), 1)
                    self.assertIn("distributed", result.jobs[0].description)
                    self.assertTrue(result.errors[0].partial)

    def test_partial_pagination_fetches_only_verified_final_attempt(self):
        from test_provider_reliability import RoutingClient, page_for, target_for

        from swe_scraper.providers.oracle import OracleProvider
        from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider

        for provider in (OracleProvider(), SmartRecruitersProvider()):
            pages = iter(
                [
                    page_for(provider, ["stale"], 2, True),
                    page_for(provider, ["changed"], 3, True),
                    page_for(provider, ["current"], 3, True),
                    {},
                ]
            )
            client = RoutingClient(provider, lambda _, pages=pages: next(pages))
            result = scan_targets(
                [target_for(provider)], client=client, allow_partial=True, filter_swe=False
            )
            self.assertEqual([j.source_job_id for j in result.jobs], ["current"])
            self.assertEqual(client.detail_ids, ["current"])
            self.assertEqual(result.errors[0].details[0]["stage"], "pagination")

    def test_bad_record_does_not_discard_single_page_provider(self):
        cases = {
            "greenhouse": {
                "jobs": [
                    {
                        "id": "good",
                        "title": "Software Intern",
                        "absolute_url": "https://example.test/good",
                    },
                    None,
                ]
            },
            "lever": [
                {
                    "id": "good",
                    "text": "Software Intern",
                    "applyUrl": "https://example.test/good",
                },
                {"id": "bad"},
            ],
            "ashby": {"jobs": [{"id": "good", "title": "Software Intern"}, {}]},
        }
        for provider, payload in cases.items():
            client = mock.Mock()
            client.get_json.return_value = payload
            target = Target(provider, "Example", "example")
            with self.subTest(provider=provider):
                result = scan_targets([target], client=client, allow_partial=True)
                self.assertEqual([j.source_job_id for j in result.jobs], ["good"])
                self.assertTrue(result.errors[0].partial)
                self.assertEqual(result.errors[0].details[0]["stage"], "record")
                self.assertEqual(scan_targets([target], client=client).jobs, ())

    def test_interrupt_saves_other_already_complete_futures(self):
        class Provider:
            def fetch(self, target, client):
                return [job(target.slug)]

        targets = [Target("example", "Example", str(i)) for i in range(4)]

        def progress(value):
            if value.completed == 1:
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ScanCheckpointStore(
                output=root / "out.json",
                targets=targets,
                scan_options={},
                version="test",
                resume=False,
                cache_root=root / "cache",
            )

            # Both submitted tasks finish before the first callback interrupts.
            def all_finished(futures, **kwargs):
                return concurrent.futures.wait(futures)

            with (
                mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()),
                mock.patch("swe_scraper.scanner.wait", side_effect=all_finished),
                self.assertRaises(KeyboardInterrupt),
            ):
                scan_targets(
                    targets,
                    client=object(),
                    max_workers=2,
                    progress_callback=progress,
                    checkpoint_store=store,
                )
            self.assertEqual(
                [store.load(i, target) is not None for i, target in enumerate(targets)],
                [True, True, False, False],
            )

    def test_deadlines_validate_before_fetch_and_propagate_to_detail_threads(self):
        from swe_scraper.execution import check_cancelled

        class SlowDetail(DetailClient):
            def get_json(self, url, **kwargs):
                if url.endswith("/postings"):
                    return super().get_json(url, **kwargs)
                while True:
                    check_cancelled()
                    time.sleep(0.002)

        target = Target("smartrecruiters", "Example", "example")
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                scan_targets([target], client=SlowDetail(), board_timeout=value)
        result = scan_targets([target], client=SlowDetail(), board_timeout=0.02)
        self.assertEqual(result.jobs, ())
        self.assertIn("deadline", result.errors[0].error)

    def test_retry_delay_wakes_when_cancelled_after_wait_starts(self):
        from swe_scraper.execution import FetchControl, ScanCancelled, fetch_context
        from swe_scraper.providers.http import RequestsJsonClient

        entered = threading.Event()
        stopped = threading.Event()
        outcome = []

        def run():
            with fetch_context(FetchControl(stopped)):
                entered.set()
                try:
                    RequestsJsonClient()._session().get_adapter(
                        "https://"
                    ).max_retries.sleep(
                        mock.Mock(headers={"Retry-After": "3600"}, status=429)
                    )
                except ScanCancelled:
                    outcome.append("cancelled")

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(2))
        stopped.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, ["cancelled"])

    def test_workday_partial_is_opt_in_and_never_checkpointed_as_complete(self):
        target = Target(
            "workday",
            "Example",
            "Careers",
            {
                "origin": "https://example.test",
                "tenant": "example",
            },
        )
        self.assertEqual(scan_targets([target], client=WorkdayClient()).jobs, ())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ScanCheckpointStore(
                output=root / "out.json",
                targets=[target],
                scan_options={},
                version="test",
                resume=False,
                cache_root=root / "cache",
            )
            result = scan_targets(
                [target], client=WorkdayClient(), allow_partial=True, checkpoint_store=store
            )
            self.assertEqual(len(result.jobs), 20)
            self.assertEqual(len(result.errors), 1)
            self.assertTrue(result.errors[0].to_dict()["partial"])
            self.assertTrue(result.errors[0].to_dict()["details"])
            self.assertTrue(all(j.metadata["board_complete"] is False for j in result.jobs))
            self.assertIsNone(store.load(0, target))
        health = run_health_checks([target], client=WorkdayClient(), quorum=1)
        self.assertEqual(health.exit_code, 2)
        self.assertFalse(health.checks[0].pagination_complete)

    def test_one_broken_detail_keeps_successes_on_both_sides(self):
        target = Target("smartrecruiters", "Example", "example", {"detail_workers": 2})
        result = scan_targets([target], client=DetailClient(), allow_partial=True)
        self.assertEqual({j.source_job_id for j in result.jobs}, {"first", "last"})
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].to_dict()["details"][0]["stage"], "detail")
        strict = scan_targets([target], client=DetailClient())
        self.assertEqual(strict.jobs, ())
        self.assertEqual(len(strict.errors), 1)

    def test_interrupt_does_not_start_queued_boards_and_preserves_checkpoint(self):
        calls = []

        class Provider:
            def fetch(self, target, client):
                calls.append(target.slug)
                return [job(target.slug)]

        targets = [Target("example", "Example", str(i)) for i in range(8)]

        def progress(value):
            if value.completed == 1:
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ScanCheckpointStore(
                output=root / "out.json",
                targets=targets,
                scan_options={},
                version="test",
                resume=False,
                cache_root=root / "cache",
            )
            with (
                mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()),
                self.assertRaises(KeyboardInterrupt),
            ):
                scan_targets(
                    targets,
                    client=object(),
                    max_workers=1,
                    progress_callback=progress,
                    checkpoint_store=store,
                )
            self.assertEqual(calls, ["0"])
            self.assertIsNotNone(store.load(0, targets[0]))

    def test_board_deadline_is_isolated_and_plugins_keep_fetch_contract(self):
        from swe_scraper.execution import check_cancelled

        class Provider:
            def fetch(self, target, client):
                if target.slug == "slow":
                    while True:
                        check_cancelled()
                        time.sleep(0.002)
                return [job("fast")]

        targets = [Target("example", "Example", slug) for slug in ("slow", "fast")]
        with mock.patch("swe_scraper.scanner.get_provider", return_value=Provider()):
            result = scan_targets(targets, client=object(), board_timeout=0.02)
        self.assertEqual([j.source_job_id for j in result.jobs], ["fast"])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("deadline", result.errors[0].error)

    def test_retry_sleep_and_rate_wait_are_interruptible(self):
        from swe_scraper.execution import FetchControl, ScanCancelled, fetch_context
        from swe_scraper.providers.http import RequestsJsonClient

        stopped = threading.Event()
        stopped.set()
        client = RequestsJsonClient()
        with fetch_context(FetchControl(stopped)):
            with self.assertRaises(ScanCancelled):
                client._wait_for_host("https://example.test")
            retry = client._session().get_adapter("https://").max_retries
            response = mock.Mock(headers={"Retry-After": "3600"}, status=429)
            with self.assertRaises(ScanCancelled):
                retry.sleep(response)

    def test_cli_partial_export_signals_failure_and_interrupt_returns_130(self):
        target = Target("smartrecruiters", "Example", "example", {"detail_workers": 1})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            with (
                mock.patch("swe_scraper.cli.load_targets", return_value=[target]),
                mock.patch(
                    "swe_scraper.scanner.RequestsJsonClient", return_value=DetailClient()
                ),
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = cli.main(["scan", "--allow-partial", "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.read_text())["job_count"], 2)
            with (
                mock.patch("swe_scraper.cli._run_scan", side_effect=KeyboardInterrupt),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(cli.main(["scan", "--output", str(output)]), 130)


if __name__ == "__main__":
    unittest.main()
