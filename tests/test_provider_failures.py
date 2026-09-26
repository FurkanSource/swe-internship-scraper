import unittest

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.providers.ashby import AshbyProvider
from swe_scraper.providers.base import Target
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider


class RouteClient:
    def __init__(self, get=None, post=None):
        self.get = get
        self.post = post
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if isinstance(self.get, Exception):
            raise self.get
        return self.get(url, kwargs) if callable(self.get) else self.get

    def post_json(self, url, payload, **kwargs):
        self.calls.append(("POST", url, payload, kwargs))
        return self.post(url, payload, kwargs) if callable(self.post) else self.post


class SmartRecruitersFailureTests(unittest.TestCase):
    def setUp(self):
        self.provider = SmartRecruitersProvider()
        self.target = Target("smartrecruiters", "Example", "example")

    def test_target_validation_bounds(self):
        with self.assertRaisesRegex(ValueError, "identifier"):
            self.provider.validate_target(Target("smartrecruiters", "Example", ""))
        for option, value in (("page_size", 0), ("max_pages", 101)):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.provider.validate_target(
                    Target("smartrecruiters", "Example", "example", {option: value})
                )

    def test_list_contract_failures(self):
        cases = [
            ({"content": "bad", "totalFound": 0}, "malformed"),
            ({"content": [], "totalFound": "bad"}, "totalFound"),
            ({"content": [], "totalFound": 1}, "stopped"),
        ]
        for payload, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                self.provider.fetch(self.target, RouteClient(get=payload))

    def test_changed_total_repeated_page_and_bad_detail_fail_closed(self):
        calls = 0

        def changed(_url, _kwargs):
            nonlocal calls
            calls += 1
            return {
                "content": [{"id": str(calls)}],
                "totalFound": 2 if calls % 2 else 3,
            }

        with self.assertRaisesRegex(RuntimeError, "changed totalFound"):
            self.provider.fetch(self.target, RouteClient(get=changed))

        with self.assertRaisesRegex(RuntimeError, "repeated"):
            self.provider.fetch(
                self.target,
                RouteClient(
                    get=lambda _url, kwargs: {
                        "content": [{"id": "same"}],
                        "totalFound": 2,
                        "offset": kwargs.get("params", {}).get("offset", 0),
                    }
                ),
            )

        def bad_detail(url, _kwargs):
            if url.endswith("/postings"):
                return {"content": [{"id": "one"}], "totalFound": 1}
            return {}

        with self.assertRaisesRegex(RuntimeError, "malformed details"):
            self.provider.fetch(self.target, RouteClient(get=bad_detail))

    def test_detail_normalizes_additional_locations_and_remote(self):
        job = self.provider.parse_detail(
            self.target,
            {
                "id": "one",
                "name": "Platform Intern",
                "postingUrl": "https://jobs.smartrecruiters.com/example/one",
                "location": {"city": "Remote"},
                "otherLocations": [{"city": "Austin", "region": "TX"}],
                "locationType": "REMOTE",
                "jobAd": {"sections": {"description": {"text": "<b>Build</b>"}}},
            },
        )
        self.assertTrue(job.remote)
        self.assertIn("Austin, TX", job.locations)
        self.assertEqual(job.description, "Build")


class OracleFailureTests(unittest.TestCase):
    def setUp(self):
        self.provider = OracleProvider()
        self.target = Target(
            "oracle",
            "Example",
            "CX",
            {"origin": "https://example.fa.us2.oraclecloud.com"},
        )

    def test_target_validation_rejects_unsafe_or_unbounded_options(self):
        bad = [
            {"origin": "http://example.test"},
            {"origin": "https://user:pass@example.test"},
            {"origin": "https://example.test/path"},
            {"origin": "https://example.test", "page_size": 0},
            {"origin": "https://example.test", "max_pages": 201},
        ]
        for options in bad:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.provider.validate_target(Target("oracle", "Example", "CX", options))

    def test_list_contract_failures(self):
        payloads = [
            ({"items": "bad"}, "malformed"),
            (
                {
                    "items": [
                        {
                            "requisitionList": {
                                "items": [{"Title": "Missing ID"}],
                                "hasMore": False,
                            }
                        }
                    ]
                },
                "without an ID",
            ),
            (
                {
                    "items": [
                        {
                            "requisitionList": {
                                "items": [{"Id": "1", "Title": "One"}],
                                "hasMore": None,
                            }
                        }
                    ]
                },
                "omitted hasMore",
            ),
        ]
        for payload, message in payloads:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                self.provider.fetch(self.target, RouteClient(get=payload))

    def test_incomplete_and_inconsistent_pagination_fail_closed(self):
        empty = {
            "items": [
                {
                    "TotalJobsCount": 2,
                    "requisitionList": {"items": [], "hasMore": False},
                }
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "before completion"):
            self.provider.fetch(self.target, RouteClient(get=empty))

        calls = 0

        def changed(url, _kwargs):
            nonlocal calls
            if "recruitingCEJobRequisitions?" not in url:
                return {"items": [{"Id": "1", "Title": "One"}]}
            calls += 1
            return {
                "items": [
                    {
                        "TotalJobsCount": 2 if calls % 2 else 3,
                        "requisitionList": {
                            "items": [{"Id": str(calls), "Title": "One"}],
                            "hasMore": False,
                        },
                    }
                ]
            }

        target = Target(
            "oracle",
            "Example",
            "CX",
            {"origin": "https://example.fa.us2.oraclecloud.com", "page_size": 1},
        )
        with self.assertRaisesRegex(RuntimeError, "changed TotalJobsCount"):
            self.provider.fetch(target, RouteClient(get=changed))

    def test_detail_contract_and_nested_secondary_locations(self):
        def no_detail(url, _kwargs):
            if "recruitingCEJobRequisitions?" in url:
                return {
                    "items": [
                        {
                            "TotalJobsCount": 1,
                            "requisitionList": {
                                "items": [{"Id": "1", "Title": "One"}],
                                "hasMore": False,
                            },
                        }
                    ]
                }
            return {"items": []}

        with self.assertRaisesRegex(RuntimeError, "malformed details"):
            self.provider.fetch(self.target, RouteClient(get=no_detail))

        job = self.provider.parse_detail(
            self.target,
            {
                "Id": "1",
                "Title": "Platform Intern",
                "secondaryLocations": {"items": [{"Name": "Austin, TX"}]},
            },
            {
                "RequisitionId": "1",
                "Title": "Platform Intern",
                "WorkplaceType": "Remote",
                "ExternalResponsibilitiesStr": "<p>Build</p>",
            },
        )
        self.assertIn("Austin, TX", job.locations)
        self.assertEqual(job.description, "Build")


class AshbyFailureTests(unittest.TestCase):
    def test_graphql_teams_fallback_and_error_paths(self):
        responses = iter(
            [
                {"errors": ['Cannot query field "jobBoard"']},
                {
                    "data": {
                        "jobBoard": {
                            "jobPostings": [
                                {
                                    "id": "1",
                                    "title": "Intern",
                                    "locationName": "New York",
                                    "secondaryLocations": [{"locationName": "Remote"}],
                                }
                            ]
                        }
                    }
                },
            ]
        )
        client = RouteClient(
            get=RuntimeError("unavailable"),
            post=lambda _url, _payload, _kwargs: next(responses),
        )
        jobs = AshbyProvider().fetch(Target("ashby", "Example", "example"), client)
        self.assertEqual(jobs[0].locations, ("New York", "Remote"))

        client = RouteClient(
            get=RuntimeError("unavailable"),
            post={"errors": ["permission denied"]},
        )
        with self.assertRaisesRegex(RuntimeError, "GraphQL errors"):
            AshbyProvider().fetch(Target("ashby", "Example", "example"), client)

    def test_pagination_failures_are_visible(self):
        def graph(page_info):
            return {
                "data": {"jobBoard": {"jobPostings": {"nodes": [], "pageInfo": page_info}}}
            }

        for page_info, message in [
            ({"hasNextPage": True}, "endCursor"),
            ({"hasNextPage": True, "endCursor": "same"}, "repeated"),
        ]:
            calls = 0

            def post(
                _url,
                _payload,
                _kwargs,
                current_message=message,
                current_page_info=page_info,
            ):
                nonlocal calls
                calls += 1
                if current_message == "repeated" and calls == 1:
                    return graph({"hasNextPage": True, "endCursor": "same"})
                return graph(current_page_info)

            with (
                self.subTest(message=message),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                AshbyProvider().fetch(
                    Target("ashby", "Example", "example"),
                    RouteClient(get=RuntimeError("unavailable"), post=post),
                )

        with self.assertRaisesRegex(RuntimeError, "not found"):
            AshbyProvider().fetch(
                Target("ashby", "Example", "example"),
                RouteClient(
                    get=RuntimeError("unavailable"), post={"data": {"jobBoard": None}}
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
