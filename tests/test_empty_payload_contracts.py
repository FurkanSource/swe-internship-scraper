"""Healthy empty boards require a valid public response, not a dropped payload."""

import unittest
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.providers.ashby import AshbyProvider
from swe_scraper.providers.base import Target
from swe_scraper.providers.greenhouse import GreenhouseProvider
from swe_scraper.providers.lever import LeverProvider
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider


class EmptyPayloadContractTests(unittest.TestCase):
    def test_paginated_boards_reject_dropped_rows_and_inconsistent_counts(self):
        cases = [
            (SmartRecruitersProvider(), {"content": [{}], "totalFound": 1}),
            (SmartRecruitersProvider(), {"content": [], "totalFound": False}),
            (SmartRecruitersProvider(), {"content": [{"id": "1"}], "totalFound": 0}),
            (
                SmartRecruitersProvider(),
                {
                    "content": [{"id": "1"}, {"id": "1"}],
                    "totalFound": 2,
                },
            ),
            (
                OracleProvider(),
                {
                    "items": [
                        {
                            "requisitionList": {
                                "items": [None],
                                "hasMore": False,
                            }
                        }
                    ]
                },
            ),
            (OracleProvider(), {"items": [], "hasMore": 0}),
            (
                OracleProvider(),
                {
                    "items": [
                        {
                            "TotalJobsCount": False,
                            "requisitionList": {"items": [], "hasMore": False},
                        }
                    ]
                },
            ),
            (
                OracleProvider(),
                {
                    "items": [
                        {
                            "TotalJobsCount": 0,
                            "requisitionList": {"items": [{"Id": "1"}], "hasMore": False},
                        }
                    ]
                },
            ),
            (
                OracleProvider(),
                {
                    "items": [
                        {
                            "requisitionList": {
                                "items": [{"Id": "1"}, {"Id": "1"}],
                                "hasMore": False,
                            }
                        }
                    ]
                },
            ),
        ]
        for provider, payload in cases:
            with self.subTest(provider=provider.name, payload=payload):
                client = mock.Mock()
                client.get_json.return_value = payload
                with self.assertRaises(RuntimeError):
                    provider.fetch(
                        Target(
                            provider.name,
                            "Example",
                            "example",
                            {"origin": "https://example.test"},
                        ),
                        client,
                    )
                self.assertEqual(client.get_json.call_count, 1)

    def test_valid_empty_lists_remain_successful(self):
        for provider, payload in (
            (GreenhouseProvider(), {"jobs": []}),
            (LeverProvider(), []),
            (AshbyProvider(), {"jobs": []}),
        ):
            with self.subTest(provider=provider.name):
                client = mock.Mock()
                client.get_json.return_value = payload
                self.assertEqual(
                    provider.fetch(Target(provider.name, "Example", "example"), client), []
                )

    def test_malformed_boards_and_dropped_rows_fail(self):
        cases = [
            (GreenhouseProvider(), [None, {}, {"jobs": None}, {"jobs": [{}]}]),
            (LeverProvider(), [None, {}, [None], [{}]]),
            (AshbyProvider(), [None, {}, {"jobs": None}, {"jobs": [{}]}]),
        ]
        for provider, payloads in cases:
            for payload in payloads:
                with self.subTest(provider=provider.name, payload=payload):
                    client = mock.Mock()
                    client.get_json.return_value = payload
                    with self.assertRaises(ValueError):
                        provider.fetch(Target(provider.name, "Example", "example"), client)

    def test_ashby_empty_graphql_shapes_and_invalid_page_info(self):
        provider = AshbyProvider()
        for postings in ([], {"nodes": [], "pageInfo": {"hasNextPage": False}}):
            client = mock.Mock()
            client.get_json.side_effect = RuntimeError("public API unavailable")
            client.post_json.return_value = {
                "data": {"jobBoard": {"jobPostings": postings}}
            }
            self.assertEqual(
                provider.fetch(Target("ashby", "Example", "example"), client), []
            )
        for postings in (
            None,
            {},
            {"nodes": []},
            {"nodes": [], "pageInfo": {"hasNextPage": "false"}},
        ):
            client = mock.Mock()
            client.get_json.side_effect = RuntimeError("public API unavailable")
            client.post_json.return_value = {
                "data": {"jobBoard": {"jobPostings": postings}}
            }
            with self.subTest(postings=postings), self.assertRaises(ValueError):
                provider.fetch(Target("ashby", "Example", "example"), client)
