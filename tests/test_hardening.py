import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from _bootstrap import ROOT

from swe_scraper import cli
from swe_scraper.config import load_targets
from swe_scraper.dedupe import deduplicate
from swe_scraper.health import evaluate_health
from swe_scraper.models import Job, ProviderFailure, ScanResult
from swe_scraper.notifications import JsonLinesNotifier
from swe_scraper.providers.registry import ProviderRegistry


def make_job(**overrides):
    values = {
        "id": "greenhouse:acme:1",
        "company": "Acme, Inc.",
        "title": "Software Engineer Intern - Summer 2027",
        "application_url": "https://job-boards.greenhouse.io/acme/jobs/1",
        "provider": "greenhouse",
        "source_job_id": "1",
        "locations": ("New York, NY",),
    }
    values.update(overrides)
    return Job(**values)


class HardeningTests(unittest.TestCase):
    def test_one_canonical_catalog_exposes_priority_all_and_canary_profiles(self):
        priority = load_targets(profile="priority")
        all_targets = load_targets(profile="all")
        canaries = load_targets(profile="canary")

        self.assertGreater(len(all_targets), 1000)
        self.assertGreater(len(priority), 40)
        self.assertLess(len(priority), len(all_targets))
        self.assertEqual(
            {target.provider for target in canaries},
            {
                "greenhouse",
                "lever",
                "ashby",
                "workday",
                "smartrecruiters",
                "oracle",
            },
        )
        self.assertEqual(len(canaries), 18)
        provider_counts = Counter(target.provider for target in all_targets)
        for provider in {target.provider for target in canaries}:
            self.assertGreaterEqual(provider_counts[provider], 10)
        self.assertFalse((ROOT / "tools" / "targets.json").exists())
        self.assertFalse((ROOT / "tools" / "all_targets.json").exists())

    def test_provider_registry_accepts_explicit_extensions(self):
        class ExampleProvider:
            name = "example"

            def fetch(self, target, client):
                return []

        registry = ProviderRegistry(discover=False)
        registry.register(ExampleProvider())
        self.assertEqual(registry.get("EXAMPLE").name, "example")
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(ExampleProvider())

    def test_semantic_dedupe_merges_only_high_confidence_equivalents(self):
        equivalent = make_job(
            id="lever:acme:abc",
            company="Acme",
            title="Summer 2027 Software Engineering Internship",
            application_url="https://jobs.lever.co/acme/abc",
            provider="lever",
            source_job_id="abc",
            locations=("New York, NY", "Remote"),
        )
        result = deduplicate([make_job(), equivalent])
        self.assertEqual(len(result), 1)
        evidence = result[0].metadata["deduplication"]
        self.assertEqual(evidence["reason"], "company_title_location")
        self.assertGreaterEqual(evidence["confidence"], 0.9)
        self.assertEqual(set(evidence["providers"]), {"greenhouse", "lever"})
        self.assertEqual(evidence["matches"][0]["source_job_id"], "abc")

        different_market = make_job(
            id="lever:acme:west",
            company="Acme",
            application_url="https://jobs.lever.co/acme/west",
            provider="lever",
            source_job_id="west",
            locations=("San Francisco, CA",),
        )
        self.assertEqual(len(deduplicate([make_job(), different_market])), 2)

    def test_dedupe_uses_provider_source_identity_when_urls_change(self):
        moved = make_job(application_url="https://careers.example.com/new/application/1")
        result = deduplicate([make_job(), moved])
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].metadata["deduplication"]["reason"],
            "source_identity",
        )

    def test_json_lines_notifier_emits_machine_readable_new_job_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            JsonLinesNotifier(path).notify([make_job()])
            event = json.loads(path.read_text(encoding="utf-8").strip())
        self.assertEqual(event["event"], "jobs.discovered")
        self.assertEqual(event["job_count"], 1)
        self.assertEqual(event["jobs"][0]["company"], "Acme, Inc.")

    def test_health_requires_each_expected_provider_to_return_jobs(self):
        healthy = ScanResult.from_iterables(
            [
                make_job(),
                make_job(
                    id="lever:acme:2",
                    provider="lever",
                    source_job_id="2",
                    application_url="https://jobs.lever.co/acme/2",
                ),
            ]
        )
        report = evaluate_health(healthy, {"greenhouse", "lever"})
        self.assertTrue(report.healthy)
        self.assertEqual(report.provider_counts, {"greenhouse": 1, "lever": 1})

        failed = ScanResult.from_iterables(
            [make_job()],
            [ProviderFailure("lever", "Acme", "acme", "timeout")],
        )
        report = evaluate_health(failed, {"greenhouse", "lever"})
        self.assertFalse(report.healthy)
        self.assertIn("lever", report.missing_providers)

    def test_public_cli_is_scraper_only_and_includes_health_command(self):
        help_text = cli.build_parser().format_help()
        for command in ("scan", "validate", "watch", "health"):
            self.assertIn(command, help_text)
        self.assertNotIn("tracker", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
