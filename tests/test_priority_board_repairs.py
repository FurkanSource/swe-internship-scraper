"""Regression checks for repaired priority boards and compact Greenhouse listings."""

import unittest

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.config import load_targets
from swe_scraper.providers.base import Target
from swe_scraper.providers.greenhouse import GreenhouseProvider


class GreenhouseListingClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("/jobs"):
            return {
                "jobs": [
                    {
                        "id": 1,
                        "title": "Software Engineer Intern",
                        "absolute_url": "https://jobs.example.test/1",
                        "location": {"name": "New York"},
                    },
                    {
                        "id": 2,
                        "title": "Senior Accountant",
                        "absolute_url": "https://jobs.example.test/2",
                        "location": {"name": "Remote"},
                    },
                ]
            }
        if url.endswith("/jobs/1"):
            return {
                "id": 1,
                "title": "Software Engineer Intern",
                "absolute_url": "https://jobs.example.test/1",
                "location": {"name": "New York"},
                "content": "<p>Build systems.</p>",
            }
        raise AssertionError(f"Unexpected request: {url}")


class PriorityBoardRepairTests(unittest.TestCase):
    def test_compact_listing_preserves_ids_and_direct_urls(self):
        target = Target(
            "greenhouse", "Anduril", "andurilindustries", {"listing_only": True}
        )
        client = GreenhouseListingClient()
        jobs = GreenhouseProvider().fetch(target, client)
        self.assertEqual([job.source_job_id for job in jobs], ["1", "2"])
        self.assertEqual(jobs[0].application_url, "https://jobs.example.test/1")
        self.assertEqual(jobs[0].description, "")
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(client.calls[0].endswith("/jobs"))

    def test_keyword_scan_fetches_only_candidate_details(self):
        target = Target(
            "greenhouse", "Anduril", "andurilindustries", {"listing_only": True}
        )
        client = GreenhouseListingClient()
        jobs = GreenhouseProvider().fetch_candidates_with_details(target, client)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].description, "Build systems.")
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(client.calls[1].endswith("/jobs/1"))

    def test_invalid_listing_option_fails_before_network(self):
        with self.assertRaisesRegex(ValueError, "listing_only must be a boolean"):
            GreenhouseProvider().fetch(
                Target(
                    "greenhouse", "Anduril", "andurilindustries", {"listing_only": "yes"}
                ),
                GreenhouseListingClient(),
            )

    def test_priority_catalog_uses_verified_boards(self):
        targets = load_targets(profile="priority")
        identities = {(target.provider, target.slug) for target in targets}
        self.assertIn(("greenhouse", "brex"), identities)
        self.assertIn(("ashby", "applied"), identities)
        self.assertIn(("ashby", "Talos-Trading"), identities)
        self.assertNotIn(("greenhouse", "appliedintuition"), identities)
        for slug in ("anthropic", "brex", "retool", "superhuman", "talos"):
            self.assertNotIn(("ashby", slug), identities)
        self.assertNotIn(("workday", "TD_Bank_Careers"), identities)


if __name__ == "__main__":
    unittest.main()
