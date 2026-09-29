"""Completeness and scope regressions for large Workday boards."""

import unittest
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.providers.base import Target
from swe_scraper.providers.workday import WorkdayProvider
from swe_scraper.scanner import scan_targets


def target(**options):
    return Target(
        "workday",
        "Example",
        "careers",
        {
            "origin": "https://example.myworkdayjobs.com",
            "tenant": "example",
            **options,
        },
    )


class WorkdayMajorTests(unittest.TestCase):
    def test_explicit_ceiling_above_twenty_pages_is_honored(self):
        client = mock.Mock()
        client.post_json.side_effect = [
            {
                "total": 401,
                "jobPostings": [
                    {
                        "title": "Software Engineer Intern",
                        "externalPath": f"/job/Intern_R{i}",
                    }
                    for i in range(page * 20, min((page + 1) * 20, 401))
                ],
            }
            for page in range(21)
        ]
        jobs = WorkdayProvider().fetch(target(max_pages=25), client)
        self.assertEqual(len(jobs), 401)
        self.assertEqual(client.post_json.call_count, 21)

    def test_invalid_bounds_fail_before_network(self):
        for options in (
            {"page_size": 0},
            {"page_size": 1001},
            {"max_pages": 0},
            {"max_pages": 1001},
            {"max_pages": True},
            {"page_size": 1.5},
            {"search_text": None},
        ):
            client = mock.Mock()
            with self.subTest(options=options), self.assertRaises(ValueError):
                WorkdayProvider().fetch(target(**options), client)
            client.post_json.assert_not_called()

    def test_default_full_fetch_does_not_restrict_search_to_intern(self):
        client = mock.Mock()
        client.post_json.return_value = {"total": 0, "jobPostings": []}
        WorkdayProvider().fetch(target(), client)
        self.assertEqual(client.post_json.call_args.args[1]["searchText"], "")

    def test_unconstrained_candidate_scan_never_fetches_details(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 2,
            "jobPostings": [
                {"title": "Software Co-op", "externalPath": "/job/Role_R1"},
                {"title": "Senior Engineer", "externalPath": "/job/Role_R2"},
            ],
        }
        result = scan_targets([target()], client=client)
        self.assertFalse(result.errors)
        self.assertEqual(len(result.jobs), 1)
        client.get_json.assert_not_called()

    def test_invalid_row_reports_offset_and_reason(self):
        client = mock.Mock()
        client.post_json.return_value = {"total": 1, "jobPostings": [{"title": "Intern"}]}
        with self.assertRaisesRegex(RuntimeError, r"offset 0.*row 0.*externalPath"):
            WorkdayProvider().fetch(target(), client)

    def test_malformed_listing_page_gets_one_fresh_recheck(self):
        client = mock.Mock()
        client.post_json.side_effect = [
            {"total": 1, "jobPostings": [{"bulletFields": ["R1"]}]},
            {
                "total": 1,
                "jobPostings": [
                    {"title": "Software Co-op", "externalPath": "/job/Role_R1"}
                ],
            },
        ]
        jobs = WorkdayProvider().fetch(target(), client)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(client.post_json.call_count, 2)
        self.assertEqual(
            client.post_json.call_args_list[0], client.post_json.call_args_list[1]
        )

    def test_persistent_malformed_rows_fail_after_one_recheck(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [{"bulletFields": ["R1"]}],
        }
        with self.assertRaisesRegex(RuntimeError, r"offset 0.*title"):
            WorkdayProvider().fetch_candidates_with_details(target(), client)
        self.assertEqual(client.post_json.call_count, 2)
        client.get_json.assert_not_called()

    def test_complete_listings_precede_candidate_details_and_keep_coop(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 3,
            "jobPostings": [
                {"title": title, "externalPath": f"/job/Role_R{i}"}
                for i, title in enumerate(
                    (
                        "Software Engineer Co-op",
                        "Software Engineer Intern",
                        "Senior Engineer",
                    )
                )
            ],
        }
        client.get_json.return_value = {
            "jobPostingInfo": {"jobDescription": "<p>Build in Python &amp; C++.</p>"}
        }
        jobs = WorkdayProvider().fetch_candidates_with_details(
            target(detail_workers=1), client
        )
        self.assertEqual(len(jobs), 2)
        self.assertEqual(client.get_json.call_count, 2)
        self.assertEqual(jobs[0].description, "Build in Python & C++.")
        self.assertTrue(jobs[0].metadata["details_complete"])
        self.assertEqual(client.post_json.call_args.args[1]["searchText"], "")

    def test_explicit_scope_is_preserved_and_bad_pagination_never_fetches_details(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 2,
            "jobPostings": [
                {"title": "Software Intern", "externalPath": "/job/Role_R1"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "incomplete pagination"):
            WorkdayProvider().fetch_candidates_with_details(
                target(search_text="co-op"), client
            )
        client.get_json.assert_not_called()
        self.assertEqual(client.post_json.call_args.args[1]["searchText"], "co-op")

    def test_keyword_filters_in_all_jobs_mode_use_real_details(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Software Engineer",
                    "externalPath": "/job/Role_R1",
                    "bulletFields": ["R1"],
                },
            ],
        }
        client.get_json.return_value = {
            "jobPostingInfo": {"jobDescription": "Build services in Python."}
        }
        with mock.patch("swe_scraper.scanner.get_provider", return_value=WorkdayProvider()):
            result = scan_targets(
                [target(detail_workers=1)],
                client=client,
                filter_swe=False,
                include_keywords=(value for value in ["Python"]),
            )
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(client.get_json.call_count, 1)
        self.assertEqual(len(result.jobs), 1)

    def test_listing_only_fetch_does_not_label_requisition_ids_as_duties(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Software Engineer",
                    "externalPath": "/job/Role_R1",
                    "bulletFields": ["R1"],
                },
            ],
        }
        jobs = WorkdayProvider().fetch(target(), client)
        self.assertEqual(jobs[0].description, "")
        self.assertFalse(jobs[0].metadata["details_complete"])
        client.get_json.assert_not_called()

    def test_malformed_detail_fails_instead_of_retaining_summary(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [
                {"title": "Software Intern", "externalPath": "/job/Role_R1"},
            ],
        }
        client.get_json.return_value = {"jobPostingInfo": {}}
        with self.assertRaisesRegex(RuntimeError, "malformed details"):
            WorkdayProvider().fetch_candidates_with_details(
                target(detail_workers=1), client
            )

    def test_detail_locations_make_multilocation_jobs_filterable(self):
        client = mock.Mock()
        client.post_json.return_value = {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Software Intern",
                    "externalPath": "/job/Role_R1",
                    "locationsText": "21 Locations",
                },
            ],
        }
        client.get_json.return_value = {
            "jobPostingInfo": {
                "jobDescription": "Build tools",
                "location": "Fridley, Minnesota, USA",
                "additionalLocations": ["Eatontown, New Jersey, USA"],
                "remoteType": "Remote",
            }
        }
        result = scan_targets(
            [target(detail_workers=1)], client=client, locations=["New Jersey"]
        )
        self.assertEqual(len(result.jobs), 1)
        self.assertIn("Eatontown, New Jersey, USA", result.jobs[0].locations)
        self.assertTrue(result.jobs[0].remote)


if __name__ == "__main__":
    unittest.main()
