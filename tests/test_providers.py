import unittest

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.providers.ashby import AshbyProvider
from swe_scraper.providers.base import Target
from swe_scraper.providers.greenhouse import GreenhouseProvider
from swe_scraper.providers.lever import LeverProvider
from swe_scraper.providers.workday import WorkdayProvider


class FakeClient:
    def __init__(self, get_data=None, post_data=None):
        self.get_data = get_data
        self.post_data = post_data
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.get_data

    def post_json(self, url, payload, **kwargs):
        self.calls.append(("POST", url, payload, kwargs))
        return self.post_data


class ProviderTests(unittest.TestCase):
    def test_greenhouse_normalizes_public_board(self):
        client = FakeClient(
            get_data={
                "jobs": [
                    {
                        "id": 10,
                        "title": "Software Engineer Intern",
                        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/10",
                        "location": {"name": "New York, NY"},
                        "updated_at": "2026-09-01T12:00:00Z",
                        "content": "<p>Build systems.</p>",
                    }
                ]
            }
        )
        jobs = GreenhouseProvider().fetch(Target("greenhouse", "Acme", "acme"), client)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].description, "Build systems.")

    def test_lever_normalizes_locations_and_created_time(self):
        client = FakeClient(
            get_data=[
                {
                    "id": "abc",
                    "text": "Backend Developer Intern",
                    "applyUrl": "https://jobs.lever.co/acme/abc/apply",
                    "categories": {"allLocations": ["Remote"]},
                    "createdAt": 1788200000000,
                }
            ]
        )
        jobs = LeverProvider().fetch(Target("lever", "Acme", "acme"), client)
        self.assertTrue(jobs[0].remote)
        self.assertTrue(jobs[0].posted_at.startswith("2026-"))

    def test_ashby_uses_public_posting_api(self):
        client = FakeClient(
            get_data={
                "jobs": [
                    {
                        "id": "job-1",
                        "title": "Machine Learning Intern",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/job-1",
                        "location": "San Francisco",
                        "isRemote": True,
                    }
                ]
            }
        )
        jobs = AshbyProvider().fetch(Target("ashby", "Acme", "acme"), client)
        self.assertEqual(jobs[0].provider, "ashby")
        self.assertTrue(jobs[0].remote)

    def test_ashby_falls_back_to_public_graphql_board(self):
        class FallbackClient(FakeClient):
            def get_json(self, url, **kwargs):
                raise RuntimeError("posting API unavailable")

        client = FallbackClient(
            post_data={
                "data": {
                    "jobBoard": {
                        "jobPostings": {
                            "nodes": [
                                {
                                    "id": "job-2",
                                    "title": "Software Engineering Intern",
                                    "locationName": "Remote",
                                    "isRemote": True,
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        )
        jobs = AshbyProvider().fetch(Target("ashby", "Acme", "acme"), client)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "job-2")

    def test_workday_requires_explicit_tenant_configuration(self):
        target = Target(
            "workday",
            "Acme",
            "careers",
            {
                "origin": "https://acme.wd1.myworkdayjobs.com",
                "tenant": "acme",
                "site": "careers",
            },
        )
        client = FakeClient(
            post_data={
                "total": 1,
                "jobPostings": [
                    {
                        "title": "Software Intern",
                        "externalPath": "/job/NYC/Role_R1",
                        "jobReqId": "R1",
                        "locationsText": "New York, NY",
                    }
                ],
            }
        )
        jobs = WorkdayProvider().fetch(target, client)
        self.assertEqual(len(jobs), 1)
        self.assertIn("/careers/job/NYC/Role_R1", jobs[0].application_url)

    def test_workday_fails_closed_when_pagination_limit_is_incomplete(self):
        target = Target(
            "workday",
            "Acme",
            "careers",
            {
                "origin": "https://acme.wd1.myworkdayjobs.com",
                "tenant": "acme",
                "site": "careers",
                "page_size": 1,
                "max_pages": 1,
            },
        )
        client = FakeClient(
            post_data={
                "total": 2,
                "jobPostings": [
                    {
                        "title": "Software Intern",
                        "externalPath": "/job/NYC/Role_R1",
                        "jobReqId": "R1",
                        "locationsText": "New York, NY",
                    }
                ],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "pagination limit"):
            WorkdayProvider().fetch(target, client)

    def test_workday_caps_page_size_at_public_api_limit(self):
        target = Target(
            "workday",
            "Acme",
            "careers",
            {
                "origin": "https://acme.wd1.myworkdayjobs.com",
                "tenant": "acme",
                "site": "careers",
                "page_size": 100,
            },
        )
        client = FakeClient(post_data={"total": 0, "jobPostings": []})

        WorkdayProvider().fetch(target, client)

        self.assertEqual(client.calls[0][2]["limit"], 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
