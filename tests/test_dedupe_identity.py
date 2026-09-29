import itertools
import json
import unittest

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.dedupe import deduplicate, deduplicate_with_audit, duplicate_match
from swe_scraper.models import SCHEMA_VERSION, Job, JobSource, MatchEvidence


def job(identity="1001", namespace="tenant-a", **overrides):
    values = {
        "id": f"oracle:{namespace}:{identity}",
        "provider": "oracle",
        "source_job_id": identity,
        "company": "Example Systems",
        "title": "Software Engineer Intern Summer 2027",
        "locations": ("New York, NY",),
        "application_url": f"https://{namespace or 'unknown'}.example.test/jobs/{identity}",
        "metadata": {"source_namespace": namespace} if namespace is not None else {},
    }
    values.update(overrides)
    return Job(**values)


class ExactIdentityTests(unittest.TestCase):
    def test_same_1001_in_different_tenants_stays_separate(self):
        left = job(company="Alpha", locations=("New York, NY",))
        right = job(namespace="tenant-b", company="Beta", locations=("London, UK",))
        self.assertIsNone(duplicate_match(left, right))
        self.assertEqual(len(deduplicate([left, right])), 2)

    def test_same_company_title_location_different_requisitions_stay_separate(self):
        left, right = job("1001"), job("1002")
        self.assertIsNone(duplicate_match(left, right))
        result = deduplicate_with_audit([left, right])
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(len(result.potential_duplicates), 1)
        self.assertTrue(all(not item.merge_evidence for item in result.jobs))

    def test_scoped_identity_follows_changed_url(self):
        left = job()
        right = job(application_url="https://careers.example.test/moved/1001")
        self.assertEqual(duplicate_match(left, right).reason, "source_identity")
        merged = deduplicate([left, right])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].sources), 2)
        self.assertEqual(merged[0].merge_evidence[0].source.namespace, "tenant-a")
        self.assertEqual(
            merged[0].metadata["deduplication"]["matches"][0]["namespace"], "tenant-a"
        )

    def test_source_ids_and_namespaces_are_case_sensitive(self):
        pairs = [
            (job("Req-1001"), job("req-1001")),
            (job(namespace="Tenant-A"), job(namespace="tenant-a")),
        ]
        for left, right in pairs:
            # Hostnames are case insensitive; use different paths as well.
            right = Job.from_mapping(
                {
                    **right.to_dict(),
                    "application_url": right.application_url + "/other",
                    "sources": [],
                }
            )
            with self.subTest(left=left.source_job_id, namespace=left.sources[0].namespace):
                self.assertEqual(len(deduplicate([left, right])), 2)

    def test_same_namespace_and_id_in_different_providers_stays_separate(self):
        left = job()
        right = job(provider="workday", application_url="https://other.test/job/1001")
        self.assertEqual(len(deduplicate([left, right])), 2)

    def test_unknown_or_blank_namespace_only_matches_by_url(self):
        for namespace in (None, "", "   "):
            for other_namespace in (namespace, "tenant-a"):
                with self.subTest(namespace=namespace, other=other_namespace):
                    left = job(namespace=namespace)
                    right = job(
                        namespace=other_namespace,
                        application_url="https://careers.example.test/moved/1001",
                    )
                    self.assertEqual(len(deduplicate([left, right])), 2)

    def test_exact_canonical_url_wins_despite_different_scope_and_content(self):
        left = job(application_url="https://boards.greenhouse.io/acme/jobs/1001?utm_x=a")
        right = job(
            "2002",
            namespace="tenant-b",
            provider="feed",
            company="Other Name",
            title="Different title",
            locations=("London, UK",),
            application_url="https://job-boards.greenhouse.io/acme/jobs/1001/#apply",
        )
        self.assertEqual(duplicate_match(left, right).reason, "exact_url")
        self.assertEqual(len(deduplicate([left, right])), 1)

    def test_unscoped_sources_are_not_backfilled_from_job_metadata(self):
        left = job(sources=(JobSource("feed", "1001", "https://feed.test/old"),))
        right = job("2002", sources=(JobSource("feed", "1001", "https://feed.test/new"),))
        self.assertEqual(len(deduplicate([left, right])), 2)

    def test_legacy_provenance_url_still_links_records(self):
        shared = "https://feed.test/original/1001"
        left = job(sources=(JobSource("feed", "old", shared),))
        right = job("2002", sources=(JobSource("other", "new", shared),))
        self.assertEqual(len(deduplicate([left, right])), 1)

    def test_scoped_provenance_can_supply_identity(self):
        left = job(sources=(JobSource("feed", "1001", "https://feed.test/old", "board"),))
        right = job(
            "2002", sources=(JobSource("feed", "1001", "https://feed.test/new", "board"),)
        )
        self.assertEqual(len(deduplicate([left, right])), 1)

    def test_transitive_exact_bridge_and_primary_are_stable(self):
        records = [
            job("a", description="The most complete original description."),
            job("b", locations=("Boston",)),
            job("b", application_url="https://tenant-a.example.test/jobs/a"),
            job("1001", namespace="tenant-b"),
        ]
        outputs = []
        for order in itertools.permutations(records):
            result = deduplicate(order)
            self.assertEqual(len(result), 2)
            merged = next(item for item in result if len(item.sources) == 3)
            self.assertEqual(merged.id, records[0].id)
            self.assertEqual(
                {item.reason for item in merged.merge_evidence},
                {"exact_url", "source_identity"},
            )
            outputs.append([item.to_dict() for item in result])
        self.assertTrue(all(output == outputs[0] for output in outputs))

    def test_primary_is_chosen_from_originals_before_location_enrichment(self):
        url = "https://careers.example.test/shared"
        records = [
            job("a", application_url=url, locations=("Austin", "Boston")),
            job("b", application_url=url, locations=("Chicago", "Denver")),
            job("c", application_url=url, locations=("El Paso", "Fresno", "Houston")),
        ]
        for order in itertools.permutations(records):
            self.assertEqual(deduplicate(order)[0].id, records[2].id)

    def test_tied_records_and_provenance_have_deterministic_output(self):
        records = [
            job(id="same", company="Example Systems", description="AAAA"),
            job(id="same", company="Example Systems, Inc.", description="BBBB"),
        ]
        outputs = {
            json.dumps([item.to_dict() for item in deduplicate(order)], sort_keys=True)
            for order in itertools.permutations(records)
        }
        self.assertEqual(len(outputs), 1)

    def test_primary_namespace_metadata_and_sources_survive_repeat_dedupe(self):
        shared = "https://careers.example.test/shared"
        left = job(namespace="tenant-a", application_url=shared, description="Details")
        right = job(namespace="tenant-z", application_url=shared)
        merged = deduplicate([left, right])[0]
        self.assertEqual(merged.metadata["source_namespace"], "tenant-a")
        self.assertEqual(
            {source.namespace for source in merged.sources}, {"tenant-a", "tenant-z"}
        )
        unrelated = job(
            namespace="tenant-z",
            application_url="https://other.test/1002",
            source_job_id="1002",
        )
        restored = Job.from_mapping(merged.to_dict())
        self.assertEqual(len(deduplicate([restored, unrelated])), 2)
        self.assertEqual(deduplicate([restored])[0].to_dict(), restored.to_dict())


class SimilarityAuditTests(unittest.TestCase):
    def test_former_alias_positive_is_uncertain_audit_only(self):
        left = job(
            company="Example Systems, Inc.", title="Software Engineering Intern Summer 2027"
        )
        right = job(
            "2002",
            provider="lever",
            company="Example Systems",
            title="Software Developer Intern Summer 2027",
            locations=("NYC",),
        )
        result = deduplicate_with_audit([left, right])
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(len(result.potential_duplicates), 1)
        candidate = result.potential_duplicates[0]
        self.assertEqual(candidate.reason, "similar_company_title_location")
        self.assertLess(candidate.confidence, 0.93)

    def test_explicit_us_uk_remote_boundaries_are_preserved_in_audit(self):
        for remote in (True, False):
            for us in ("Remote - US", "Remote, United States", "U.S. Remote", "Remote USA"):
                for uk in (
                    "Remote - UK",
                    "United Kingdom (Remote)",
                    "Remote, GB",
                    "U.K. Remote",
                ):
                    with self.subTest(us=us, uk=uk, remote=remote):
                        result = deduplicate_with_audit(
                            [
                                job("1001", remote=remote, locations=(us,)),
                                job("1002", remote=remote, locations=(uk,)),
                            ]
                        )
                        self.assertEqual(len(result.jobs), 2)
                        self.assertEqual(result.potential_duplicates, ())

    def test_remote_boolean_does_not_erase_explicit_country(self):
        result = deduplicate_with_audit(
            [
                job("1001", remote=True, locations=("United States", "Remote")),
                job("1002", remote=True, locations=("United Kingdom", "Remote")),
            ]
        )
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(result.potential_duplicates, ())

    def test_same_remote_country_aliases_remain_reviewable(self):
        result = deduplicate_with_audit(
            [
                job("1001", remote=True, locations=("Remote - US",)),
                job("1002", remote=True, locations=("United States (Remote)",)),
            ]
        )
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(len(result.potential_duplicates), 1)

    def test_multiple_locations_do_not_bridge_semantic_groups(self):
        result = deduplicate_with_audit(
            [
                job("a", locations=("Austin",)),
                job("b", locations=("Austin", "Boston")),
                job("c", locations=("Boston",)),
            ]
        )
        self.assertEqual(len(result.jobs), 3)
        self.assertEqual(len(result.potential_duplicates), 2)

    def test_empty_normalized_company_is_not_a_similarity_signal(self):
        result = deduplicate_with_audit([job("a", company="Inc."), job("b", company="LLC")])
        self.assertEqual(result.potential_duplicates, ())


class ProvenanceCompatibilityTests(unittest.TestCase):
    def test_default_source_uses_explicit_metadata_namespace(self):
        self.assertEqual(job().sources[0].namespace, "tenant-a")
        self.assertEqual(job(namespace=None).sources[0].namespace, "")
        self.assertEqual(SCHEMA_VERSION, 2)

    def test_v1_and_old_v2_remain_readable_and_round_trip(self):
        original = job(namespace=None).to_dict()
        original["sources"][0].pop("namespace", None)
        for version in (1, 2):
            payload = dict(original)
            if version == 1:
                payload.pop("sources")
                payload.pop("merge_evidence")
            with self.subTest(version=version):
                restored = Job.from_mapping(payload)
                self.assertEqual(restored.sources[0].namespace, "")
                self.assertEqual(Job.from_mapping(restored.to_dict()), restored)

    def test_v1_with_explicit_namespace_synthesizes_scoped_source(self):
        payload = job().to_dict()
        payload.pop("sources")
        payload.pop("merge_evidence")
        self.assertEqual(Job.from_mapping(payload).sources[0].namespace, "tenant-a")

    def test_v2_namespace_and_evidence_round_trip_without_folding_raw_id(self):
        source = JobSource(
            "oracle", " Req-Aa1001 ", "https://example.test/1001", "Tenant/A"
        )
        record = job(
            " Req-Aa1001 ",
            id="oracle:Tenant/A:1001",
            application_url="https://example.test/1001",
            sources=(source,),
            merge_evidence=(MatchEvidence("source_identity", 1.0, source),),
        )
        self.assertEqual(JobSource.from_mapping(source.to_dict()), source)
        self.assertEqual(Job.from_mapping(record.to_dict()), record)

    def test_legacy_sources_stay_unknown_despite_explicit_primary_metadata(self):
        payload = job().to_dict()
        payload["sources"][0].pop("namespace", None)
        self.assertEqual(Job.from_mapping(payload).sources[0].namespace, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
