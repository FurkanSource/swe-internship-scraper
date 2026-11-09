"""Regressions from November provider and workflow incidents."""

import unittest
from unittest.mock import Mock, patch

from _bootstrap import ROOT

from swe_scraper.health import _failure_category
from swe_scraper.providers.base import Target
from swe_scraper.providers.http import RequestsJsonClient
from swe_scraper.providers.workday import WorkdayProvider


class HealthRepairsTests(unittest.TestCase):
    def test_url_does_not_turn_malformed_contract_into_http_failure(self):
        self.assertEqual(
            _failure_category(
                RuntimeError("Posting at https://x.test returned malformed details")
            ),
            "contract",
        )

    def test_malformed_detail_recovers_once_and_counts_both_requests(self):
        client = RequestsJsonClient()
        with patch.object(client, "get_json", side_effect=[{}, {"title": "Intern"}]):
            payload, _ = client.get_detail_json(
                "https://x.test/job", {}, validate=lambda data: bool(data.get("title"))
            )
        self.assertEqual(payload["title"], "Intern")
        self.assertEqual(client.detail_requests, 2)

    def test_persistent_invalid_detail_stops_after_two_requests(self):
        client = RequestsJsonClient()
        with (
            patch.object(client, "get_json", return_value={}) as get,
            self.assertRaisesRegex(RuntimeError, "malformed details"),
        ):
            client.get_detail_json("https://x.test/job", {}, validate=lambda _: False)
        self.assertEqual(get.call_count, 2)

    def test_workday_recovers_title_from_same_posting_after_page_recheck(self):
        client = Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [{"jobReqId": "R1", "externalPath": "/job/Intern_R1"}],
        }
        client.get_json.return_value = {
            "jobPostingInfo": {"jobReqId": "R1", "title": "Software Intern"}
        }
        target = Target(
            "workday",
            "Example",
            "careers",
            {"origin": "https://example.myworkdayjobs.com", "tenant": "example"},
        )
        jobs = WorkdayProvider().fetch(target, client)
        self.assertEqual(jobs[0].title, "Software Intern")
        self.assertEqual(jobs[0].source_job_id, "R1")
        self.assertEqual(client.post_json.call_count, 2)
        self.assertEqual(client.get_json.call_count, 1)

    def test_workday_never_accepts_title_from_another_requisition(self):
        client = Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [{"jobReqId": "R1", "externalPath": "/job/Intern_R1"}],
        }
        client.get_json.return_value = {
            "jobPostingInfo": {"jobReqId": "R2", "title": "Software Intern"}
        }
        target = Target(
            "workday",
            "Example",
            "careers",
            {"origin": "https://example.myworkdayjobs.com", "tenant": "example"},
        )
        with self.assertRaisesRegex(RuntimeError, "malformed details"):
            WorkdayProvider().fetch(target, client)
        self.assertEqual(client.get_json.call_count, 2)

    def test_transport_error_does_not_get_an_additional_contract_retry(self):
        import requests

        client = RequestsJsonClient()
        with (
            patch.object(client, "get_json", side_effect=requests.HTTPError("503")) as get,
            self.assertRaises(requests.HTTPError),
        ):
            client.get_detail_json("https://x.test/job", {}, validate=lambda _: False)
        self.assertEqual(get.call_count, 1)

    def test_failed_title_recovery_preserves_valid_neighbors_in_partial_mode(self):
        from threading import Event

        from swe_scraper.execution import FetchControl, fetch_context
        from swe_scraper.providers.results import PartialFetchError

        client = Mock()
        client.post_json.return_value = {
            "total": 2,
            "jobPostings": [
                {"title": "Software Intern", "externalPath": "/job/Good_R1"},
                {"externalPath": "/job/Bad_R2"},
            ],
        }
        client.get_json.return_value = {}
        target = Target(
            "workday",
            "Example",
            "careers",
            {"origin": "https://example.myworkdayjobs.com", "tenant": "example"},
        )
        with (
            fetch_context(FetchControl(Event(), allow_partial=True)),
            self.assertRaises(PartialFetchError) as failure,
        ):
            WorkdayProvider().fetch(target, client)
        self.assertEqual(len(failure.exception.result.jobs), 1)
        self.assertTrue(failure.exception.result.pagination_complete)
        self.assertEqual(failure.exception.result.issues[0].stage, "record")

    def test_cancellation_prevents_contract_retry(self):
        from swe_scraper.execution import ScanCancelled
        from swe_scraper.providers._reliability import validated_detail

        fetch = Mock(return_value={})
        with (
            patch(
                "swe_scraper.providers._reliability.check_cancelled",
                side_effect=[None, ScanCancelled("stopped")],
            ),
            self.assertRaises(ScanCancelled),
        ):
            validated_detail(fetch, lambda _: False, "https://x.test/job")
        self.assertEqual(fetch.call_count, 1)

    def test_retired_catalog_boards_are_not_silently_treated_as_empty(self):
        import json

        catalog = json.loads(
            (ROOT / "src/swe_scraper/data/targets.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("glossgenius", [x["slug"] for x in catalog["greenhouse"]])
        self.assertNotIn("sanctuary", [x["slug"] for x in catalog["lever"]])
        self.assertNotIn("firetiger", [x["slug"] for x in catalog["ashby"]])
        self.assertEqual(sum(x["slug"] == "geniusai" for x in catalog["ashby"]), 1)

    def test_codeql_steps_share_one_revision_and_dependabot_groups_them(self):
        import re

        workflow = (ROOT / ".github/workflows/security.yml").read_text()
        pins = re.findall(r"github/codeql-action/\w+@([0-9a-f]{40})", workflow)
        self.assertEqual(len(pins), 2)
        self.assertEqual(len(set(pins)), 1)
        config = (ROOT / ".github/dependabot.yml").read_text()
        self.assertIn('"github/codeql-action/*"', config)


if __name__ == "__main__":
    unittest.main()
