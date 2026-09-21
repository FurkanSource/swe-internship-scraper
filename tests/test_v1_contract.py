import itertools
import json
import unittest
import urllib.parse

from _bootstrap import ROOT  # noqa: F401

from swe_scraper import cli
from swe_scraper.dedupe import deduplicate, deduplicate_with_audit
from swe_scraper.health import HealthStatus, TargetHealth, evaluate_target_health
from swe_scraper.models import SCHEMA_VERSION, Job, JobSource, MatchEvidence
from swe_scraper.providers.base import Target
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider


class RoutingClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.handler(url, kwargs)

    def post_json(self, url, payload, **kwargs):
        self.calls.append(("POST", url, payload, kwargs))
        return self.handler(url, {**kwargs, "json": payload})


def job(provider="greenhouse", source_id="1", url=""):
    return Job(
        id=f"{provider}:acme:{source_id}",
        company="Acme, Inc.",
        title="Software Engineering Intern - Summer 2027",
        application_url=url or f"https://jobs.example/{provider}/{source_id}",
        provider=provider,
        source_job_id=source_id,
        locations=("New York, NY",),
        description="Build reliable software.",
    )


class SchemaV2Tests(unittest.TestCase):
    def test_job_exports_first_class_sources_and_merge_evidence(self):
        record = job()
        payload = record.to_dict()

        self.assertEqual(SCHEMA_VERSION, 2)
        self.assertEqual(payload["sources"][0]["provider"], "greenhouse")
        self.assertEqual(payload["sources"][0]["source_job_id"], "1")
        self.assertEqual(payload["merge_evidence"], [])

        round_trip = Job.from_mapping(payload)
        self.assertEqual(round_trip.sources, record.sources)
        self.assertEqual(round_trip.merge_evidence, record.merge_evidence)

    def test_schema_v1_mapping_is_still_accepted(self):
        legacy = job().to_dict()
        legacy.pop("sources")
        legacy.pop("merge_evidence")
        loaded = Job.from_mapping(legacy)

        self.assertEqual(
            loaded.sources,
            (
                JobSource(
                    provider="greenhouse",
                    source_job_id="1",
                    application_url=legacy["application_url"],
                ),
            ),
        )

    def test_match_evidence_validates_confidence(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            MatchEvidence("semantic", 1.1, JobSource("lever", "2", "https://x/2"))


class DeterministicDedupeTests(unittest.TestCase):
    def test_merge_is_identical_for_every_input_order(self):
        records = [
            job(),
            job("lever", "abc", "https://jobs.lever.co/acme/abc"),
            job("smartrecruiters", "sr-1", "https://jobs.smartrecruiters.com/acme/sr-1"),
        ]
        outputs = {
            json.dumps([value.to_dict() for value in deduplicate(order)], sort_keys=True)
            for order in itertools.permutations(records)
        }
        self.assertEqual(len(outputs), 1)

    def test_uncertain_pair_is_reported_without_merging(self):
        left = job()
        right = Job(
            id="lever:acme:2",
            company="Acme",
            title="Software Engineer Co-op",
            application_url="https://jobs.lever.co/acme/2",
            provider="lever",
            source_job_id="2",
            locations=("NYC",),
        )
        result = deduplicate_with_audit([left, right])
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(len(result.potential_duplicates), 1)
        self.assertLess(result.potential_duplicates[0].confidence, 0.93)

    def test_different_recruiting_years_never_auto_merge(self):
        left = job()
        right = Job(
            id="lever:acme:2028",
            company="Acme",
            title="Software Engineer Intern - Summer 2028",
            application_url="https://jobs.lever.co/acme/2028",
            provider="lever",
            source_job_id="2028",
            locations=("NYC",),
        )
        self.assertEqual(len(deduplicate([left, right])), 2)


class NewProviderTests(unittest.TestCase):
    def test_smartrecruiters_lists_then_reads_public_posting_details(self):
        def route(url, kwargs):
            if url.endswith("/postings"):
                self.assertEqual(kwargs["params"]["destination"], "PUBLIC")
                return {
                    "offset": 0,
                    "limit": 100,
                    "totalFound": 1,
                    "content": [{"id": "sr-1"}],
                }
            return {
                "id": "sr-1",
                "name": "Software Engineering Intern",
                "refNumber": "ENG-1",
                "releasedDate": "2026-09-01T12:00:00Z",
                "location": {"city": "New York", "region": "NY", "country": "US"},
                "jobAd": {"sections": {"jobDescription": {"text": "<p>Build APIs.</p>"}}},
                "applyUrl": "https://jobs.smartrecruiters.com/Acme/sr-1",
                "compensation": {"min": 30, "max": 40, "currency": "USD"},
            }

        jobs = SmartRecruitersProvider().fetch(
            Target("smartrecruiters", "Acme", "Acme"), RoutingClient(route)
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "sr-1")
        self.assertIn("Build APIs", jobs[0].description)
        self.assertIn("compensation", jobs[0].metadata)

    def test_smartrecruiters_fails_closed_on_inconsistent_total(self):
        client = RoutingClient(
            lambda _url, _kwargs: {
                "offset": 0,
                "limit": 100,
                "totalFound": 2,
                "content": [{"id": "sr-1"}],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "pagination"):
            SmartRecruitersProvider().fetch(
                Target("smartrecruiters", "Acme", "Acme", {"max_pages": 1}), client
            )

    def test_oracle_requires_origin_and_reads_requisition_details(self):
        provider = OracleProvider()
        with self.assertRaisesRegex(ValueError, "origin"):
            provider.validate_target(Target("oracle", "Acme", "CX_1"))

        def route(url, _kwargs):
            if "recruitingCEJobRequisitions?" in url:
                return {
                    "count": 1,
                    "hasMore": False,
                    "offset": 0,
                    "limit": 25,
                    "items": [
                        {
                            "requisitionList": {
                                "items": [
                                    {
                                        "Id": "123",
                                        "Title": "Software Engineer Intern",
                                        "PrimaryLocation": "New York, NY",
                                        "PostedDate": "2026-09-01",
                                    }
                                ],
                                "hasMore": False,
                            }
                        }
                    ],
                }
            return {
                "items": [
                    {
                        "Id": "123",
                        "Title": "Software Engineer Intern",
                        "ExternalDescriptionStr": "<p>Build systems.</p>",
                    }
                ]
            }

        jobs = provider.fetch(
            Target(
                "oracle",
                "Acme",
                "CX_1",
                {"origin": "https://acme.fa.us2.oraclecloud.com"},
            ),
            RoutingClient(route),
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "123")
        self.assertIn("/sites/CX_1/job/123", jobs[0].application_url)

    def test_oracle_uses_total_jobs_when_child_has_more_is_false_early(self):
        def route(url, _kwargs):
            if "recruitingCEJobRequisitions?" in url:
                second = "offset%3D1" in url
                source_id = "2" if second else "1"
                return {
                    "items": [
                        {
                            "TotalJobsCount": 2,
                            "requisitionList": {
                                "items": [{"Id": source_id, "Title": f"Role {source_id}"}],
                                "hasMore": False,
                            },
                        }
                    ],
                    "hasMore": False,
                }
            source_id = "2" if 'Id="2"' in urllib.parse.unquote(url) else "1"
            return {"items": [{"Id": source_id, "Title": f"Role {source_id}"}]}

        jobs = OracleProvider().fetch(
            Target(
                "oracle",
                "Acme",
                "CX_1",
                {
                    "origin": "https://acme.fa.us2.oraclecloud.com",
                    "page_size": 1,
                },
            ),
            RoutingClient(route),
        )
        self.assertEqual([job.source_job_id for job in jobs], ["1", "2"])


class HealthAndCliTests(unittest.TestCase):
    def test_health_quorum_distinguishes_healthy_degraded_and_unhealthy(self):
        checks = [
            TargetHealth("greenhouse", "one", HealthStatus.HEALTHY, 2, 0.1),
            TargetHealth("greenhouse", "two", HealthStatus.HEALTHY, 1, 0.2),
            TargetHealth("greenhouse", "three", HealthStatus.UNHEALTHY, 0, 0.3, "timeout"),
        ]
        degraded = evaluate_target_health(checks, quorum=2)
        self.assertEqual(degraded.status, HealthStatus.DEGRADED)
        self.assertEqual(degraded.exit_code, 1)

        unhealthy = evaluate_target_health(checks[1:], quorum=2)
        self.assertEqual(unhealthy.status, HealthStatus.UNHEALTHY)
        self.assertEqual(unhealthy.exit_code, 2)

    def test_cli_exposes_provider_inventory(self):
        help_text = cli.build_parser().format_help()
        self.assertIn("providers", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
