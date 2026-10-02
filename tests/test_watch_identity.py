import itertools
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper import cli, watch
from swe_scraper.dedupe import deduplicate, duplicate_match, merge_jobs
from swe_scraper.models import Job, JobSource, ScanResult


def job(identity="1001", provider="lever", namespace="tenant-a", **overrides):
    values = {
        "id": f"{provider}:{namespace or 'unknown'}:{identity}",
        "provider": provider,
        "source_job_id": identity,
        "company": "Example Systems",
        "title": "Software Engineer Intern",
        "application_url": (
            f"https://careers.example.test/{provider}/{namespace or 'unknown'}/{identity}"
        ),
        "metadata": {"source_namespace": namespace} if namespace is not None else {},
    }
    values.update(overrides)
    return Job(**values)


def moved(record, url):
    return replace(record, application_url=url, sources=())


class WatchIdentityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "seen.json"
        self.left = job("a")
        self.right = job("b", provider="workday", namespace="tenant-b")

    def test_a_to_a_plus_b_to_b_with_primary_shift_has_no_repeat_alert(self):
        watch.save_seen(self.state, [self.left])
        merged = merge_jobs(self.left, self.right, primary=self.right)
        self.assertEqual(merged.id, self.right.id)
        self.assertEqual(watch.unseen_jobs([merged], watch.load_seen(self.state)), [])
        watch.save_seen(self.state, [merged])
        self.assertEqual(watch.unseen_jobs([self.right], watch.load_seen(self.state)), [])
        changed_url = moved(self.right, "https://new.example.test/requisition/b")
        self.assertEqual(watch.unseen_jobs([changed_url], watch.load_seen(self.state)), [])

    def test_already_seen_job_learns_secondary_aliases(self):
        watch.save_seen(self.state, [self.left])
        merged = merge_jobs(self.left, self.right, primary=self.left)
        self.assertEqual(watch.unseen_jobs([merged], watch.load_seen(self.state)), [])
        watch.save_seen(self.state, [merged])
        self.assertEqual(watch.unseen_jobs([self.right], watch.load_seen(self.state)), [])

    def test_no_overlap_observed_means_new_alert(self):
        watch.save_seen(self.state, [self.left])
        self.assertEqual(
            watch.unseen_jobs([self.right], watch.load_seen(self.state)), [self.right]
        )

    def test_any_single_exact_alias_suppresses_the_merged_job(self):
        merged = merge_jobs(self.left, self.right, primary=self.right)
        expected = {
            "url:https://careers.example.test/lever/tenant-a/a",
            "url:https://careers.example.test/workday/tenant-b/b",
            'source:["lever","tenant-a","a"]',
            'source:["workday","tenant-b","b"]',
        }
        self.assertEqual(watch.key_texts(merged), expected)
        for alias in expected:
            with self.subTest(alias=alias):
                self.assertEqual(watch.unseen_jobs([merged], {alias}), [])

    def test_primary_url_is_included_even_when_absent_from_provenance(self):
        record = job(sources=(self.right.sources[0],))
        keys = watch.key_texts(record)
        self.assertIn(watch.key_text(record), keys)
        self.assertIn(watch.key_text(self.right), keys)
        self.assertIn('source:["workday","tenant-b","b"]', keys)
        self.assertNotIn('source:["lever","tenant-a","1001"]', keys)

    def test_primary_and_secondary_urls_share_dedupe_canonicalization(self):
        variants = (
            "https://boards.greenhouse.io/acme/jobs/1001?utm_campaign=fall&gh_jid=1001",
            "https://job-boards.greenhouse.io:443/acme/jobs/1001/#apply",
        )
        for url in variants:
            with self.subTest(url=url):
                record = job(sources=(JobSource("feed", "old", url),))
                followup = job("other", application_url=variants[1], namespace=None)
                self.assertEqual(duplicate_match(record, followup).reason, "exact_url")
                watch.save_seen(self.state, [record])
                self.assertEqual(
                    watch.unseen_jobs([followup], watch.load_seen(self.state)), []
                )
                self.assertIn(
                    "url:https://job-boards.greenhouse.io/acme/jobs/1001",
                    watch.key_texts(record),
                )

    def test_meaningful_query_parameters_keep_requisitions_distinct(self):
        left = job(
            application_url="https://careers.example.test/apply?job=1", namespace=None
        )
        right = job(
            application_url="https://careers.example.test/apply?job=2", namespace=None
        )
        self.assertIsNone(duplicate_match(left, right))
        watch.save_seen(self.state, [left])
        self.assertEqual(watch.unseen_jobs([right], watch.load_seen(self.state)), [right])

    def test_namespaced_identity_follows_changed_url(self):
        for provider in ("lever", "workday"):
            with self.subTest(provider=provider):
                left = job(provider=provider)
                right = moved(left, f"https://new.example.test/{provider}/1001")
                self.assertEqual(duplicate_match(left, right).reason, "source_identity")
                watch.save_seen(self.state, [left])
                self.assertEqual(
                    watch.unseen_jobs([right], watch.load_seen(self.state)), []
                )

    def test_provider_case_folding_matches_dedupe(self):
        left = job(provider="Lever")
        right = job(provider="lever", application_url="https://new.example.test/1001")
        self.assertEqual(duplicate_match(left, right).reason, "source_identity")
        watch.save_seen(self.state, [left])
        self.assertEqual(watch.unseen_jobs([right], watch.load_seen(self.state)), [])

    def test_namespace_and_source_id_case_are_preserved(self):
        left = job("Req-A", namespace="Tenant-A")
        others = (
            job("req-a", namespace="Tenant-A"),
            job("Req-A", namespace="tenant-a"),
        )
        watch.save_seen(self.state, [left])
        for right in others:
            with self.subTest(namespace=right.sources[0].namespace, id=right.source_job_id):
                self.assertIsNone(duplicate_match(left, right))
                self.assertEqual(
                    watch.unseen_jobs([right], watch.load_seen(self.state)), [right]
                )

    def test_equal_ids_in_different_namespaces_or_providers_are_new(self):
        left = job()
        others = (
            job(namespace="tenant-b"),
            job(provider="workday"),
            job("1002"),
        )
        watch.save_seen(self.state, [left])
        self.assertEqual(
            watch.unseen_jobs(others, watch.load_seen(self.state)), list(others)
        )
        self.assertEqual(len(deduplicate([left, *others])), 4)

    def test_delimiters_in_scope_and_id_cannot_collide(self):
        left = job("c", namespace="a:b", application_url="https://example.test/left")
        right = job("b:c", namespace="a", application_url="https://example.test/right")
        self.assertEqual(len(deduplicate([left, right])), 2)
        watch.save_seen(self.state, [left])
        self.assertEqual(watch.unseen_jobs([right], watch.load_seen(self.state)), [right])
        for record in (left, right):
            with self.subTest(namespace=record.sources[0].namespace):
                changed_url = moved(record, record.application_url + "/moved")
                watch.save_seen(self.state, [record])
                self.assertEqual(
                    watch.unseen_jobs([changed_url], watch.load_seen(self.state)), []
                )

    def test_json_tuple_keys_preserve_delimiters_quotes_and_unicode(self):
        source = JobSource("Feed:API", ' Req:1,"é" ', "https://feed.test/1", 'Board:东,"x"')
        record = job(sources=(source,))
        source_keys = [key for key in watch.key_texts(record) if key.startswith("source:")]
        self.assertEqual(len(source_keys), 1)
        self.assertEqual(
            json.loads(source_keys[0].removeprefix("source:")),
            ["feed:api", 'Board:东,"x"', ' Req:1,"é" '],
        )

    def test_missing_or_blank_namespaces_use_only_record_specific_urls(self):
        for namespace in (None, "", "   "):
            with self.subTest(namespace=namespace):
                left = job(namespace=namespace)
                right = moved(left, "https://new.example.test/1001")
                watch.save_seen(self.state, [left])
                self.assertEqual(watch.key_texts(left), {watch.key_text(left)})
                self.assertIsNone(duplicate_match(left, right))
                self.assertEqual(
                    watch.unseen_jobs([right], watch.load_seen(self.state)), [right]
                )

    def test_secondary_namespace_is_never_inferred_from_primary_metadata(self):
        for namespace in ("", "   "):
            with self.subTest(namespace=namespace):
                record = job(
                    sources=(
                        JobSource("workday", "1001", "https://feed.test/old", namespace),
                    )
                )
                followup = job(provider="workday", application_url="https://feed.test/new")
                self.assertIsNone(duplicate_match(record, followup))
                watch.save_seen(self.state, [record])
                self.assertFalse(
                    any(key.startswith("source:") for key in watch.key_texts(record))
                )
                self.assertEqual(
                    watch.unseen_jobs([followup], watch.load_seen(self.state)), [followup]
                )

    def test_legacy_key_formats_remain_recognized(self):
        records = (
            (self.left, "url:https://careers.example.test/lever/tenant-a/a"),
            (job(application_url="https://"), 'source:["lever","tenant-a","1001"]'),
            (
                job(application_url="https://", namespace=None),
                'record:["lever","lever:unknown:1001","https://"]',
            ),
        )
        for record, legacy in records:
            with self.subTest(key=legacy):
                self.state.write_text(json.dumps([legacy]), encoding="utf-8")
                self.assertEqual(watch.key_text(record), legacy)
                self.assertIn(legacy, watch.key_texts(record))
                self.assertEqual(
                    watch.unseen_jobs([record], watch.load_seen(self.state)), []
                )

    def test_legacy_primary_url_links_a_new_primary_through_provenance(self):
        legacy = "url:https://careers.example.test/lever/tenant-a/a"
        self.state.write_text(json.dumps([legacy]), encoding="utf-8")
        merged = merge_jobs(self.left, self.right, primary=self.right)
        self.assertEqual(watch.unseen_jobs([merged], watch.load_seen(self.state)), [])
        watch.save_seen(self.state, [merged])
        self.assertIn(legacy, watch.load_seen(self.state))
        self.assertEqual(watch.unseen_jobs([self.right], watch.load_seen(self.state)), [])

    def test_unseen_jobs_preserves_input_order_and_does_not_mutate_seen(self):
        known = watch.key_texts(self.left)
        original = known.copy()
        third = job("c")
        self.assertEqual(
            watch.unseen_jobs((self.right, self.left, third), known), [self.right, third]
        )
        self.assertEqual(known, original)

    def test_round_tripped_provenance_retains_all_aliases(self):
        merged = merge_jobs(self.left, self.right, primary=self.right)
        restored = Job.from_mapping(merged.to_dict())
        watch.save_seen(self.state, [restored])
        self.assertEqual(
            watch.unseen_jobs([self.left, self.right], watch.load_seen(self.state)), []
        )


class WatchStateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "nested" / "seen.json"

    def test_sorted_string_list_retains_historical_keys_without_expiry(self):
        self.state.parent.mkdir()
        historical = ['source:["old","tenant","1"]', "url:https://old.test/1", "old:key"]
        self.state.write_text(json.dumps(historical), encoding="utf-8")
        os.utime(self.state, (0, 0))
        record = job()
        watch.save_seen(self.state, [record, record])
        expected = set(historical) | watch.key_texts(record)
        self.assertEqual(
            json.loads(self.state.read_text(encoding="utf-8")), sorted(expected)
        )
        watch.save_seen(self.state, [])
        self.assertEqual(watch.load_seen(self.state), expected)

    def test_atomic_replace_receives_complete_sorted_state(self):
        self.state.parent.mkdir()
        self.state.write_text('["historical:key"]\n', encoding="utf-8")
        before = self.state.read_bytes()
        record = job()
        real_replace = os.replace

        def inspect_replace(source, destination):
            source, destination = Path(source), Path(destination)
            self.assertEqual(destination, self.state)
            self.assertEqual(source.parent, self.state.parent)
            self.assertNotEqual(source, self.state)
            self.assertEqual(self.state.read_bytes(), before)
            self.assertEqual(
                json.loads(source.read_text(encoding="utf-8")),
                sorted({"historical:key"} | watch.key_texts(record)),
            )
            real_replace(source, destination)

        with mock.patch(
            "swe_scraper.watch.os.replace", side_effect=inspect_replace
        ) as atomic:
            watch.save_seen(self.state, [record])
        atomic.assert_called_once()
        self.assertFalse(Path(atomic.call_args.args[0]).exists())

    def test_failed_replace_preserves_previous_destination(self):
        self.state.parent.mkdir()
        self.state.write_text('["historical:key"]\n', encoding="utf-8")
        before = self.state.read_bytes()
        with (
            mock.patch(
                "swe_scraper.watch.os.replace", side_effect=OSError("replace failed")
            ),
            self.assertRaisesRegex(OSError, "replace failed"),
        ):
            watch.save_seen(self.state, [job()])
        self.assertEqual(self.state.read_bytes(), before)

    def test_failed_temporary_write_preserves_previous_destination(self):
        self.state.parent.mkdir()
        self.state.write_text('["historical:key"]\n', encoding="utf-8")
        before = self.state.read_bytes()
        with (
            mock.patch.object(Path, "write_text", side_effect=OSError("write failed")),
            mock.patch("swe_scraper.watch.os.replace") as atomic,
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            watch.save_seen(self.state, [job()])
        self.assertEqual(self.state.read_bytes(), before)
        atomic.assert_not_called()

    def test_missing_malformed_or_non_list_state_remains_compatible(self):
        self.assertEqual(watch.load_seen(self.state), set())
        self.state.parent.mkdir()
        for content in ("{broken", '{"seen": []}', "null", '["valid:key", 42, null]'):
            with self.subTest(content=content):
                self.state.write_text(content, encoding="utf-8")
                expected = {"valid:key"} if content.startswith("[") else set()
                self.assertEqual(watch.load_seen(self.state), expected)


class WatchNotificationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "seen.json"
        self.arguments = [
            "watch",
            "--once",
            "--state",
            str(self.state),
            "--output",
            str(Path(directory.name) / "jobs.json"),
            "--notify-jsonl",
            str(Path(directory.name) / "alerts.jsonl"),
        ]
        self.left, self.right = job("a"), job("b", provider="workday")

    def run_once(self, records, notifier):
        with (
            mock.patch(
                "swe_scraper.cli._run_scan", return_value=ScanResult(tuple(records))
            ),
            mock.patch("swe_scraper.cli._write_result", return_value=Path("unused.json")),
            mock.patch("swe_scraper.cli._console_print"),
            mock.patch("swe_scraper.cli.JsonLinesNotifier", return_value=notifier),
        ):
            return cli.main(self.arguments)

    def test_no_new_alerts_still_persist_new_aliases_in_actual_watch_flow(self):
        watch.save_seen(self.state, [self.left])
        merged = merge_jobs(self.left, self.right, primary=self.left)
        notifier = mock.Mock()
        self.assertEqual(self.run_once([merged], notifier), 0)
        self.assertEqual(self.run_once([self.right], notifier), 0)
        notifier.notify.assert_not_called()

    def test_successful_notification_precedes_alias_persistence(self):
        watch.save_seen(self.state, [self.left])
        before = self.state.read_bytes()
        notifier = mock.Mock()

        def notify(records):
            self.assertEqual(records, [self.right])
            self.assertEqual(self.state.read_bytes(), before)

        notifier.notify.side_effect = notify
        self.assertEqual(self.run_once([self.right], notifier), 0)
        notifier.notify.assert_called_once_with([self.right])
        self.assertEqual(watch.unseen_jobs([self.right], watch.load_seen(self.state)), [])
        self.assertTrue(watch.key_texts(self.right) <= watch.load_seen(self.state))

    def test_failed_notification_preserves_state_for_retry(self):
        watch.save_seen(self.state, [self.left])
        before = self.state.read_bytes()
        notifier = mock.Mock()
        notifier.notify.side_effect = OSError("notification failed")
        with self.assertRaises(SystemExit) as exit_status:
            self.run_once([self.right], notifier)
        self.assertEqual(exit_status.exception.code, 2)
        notifier.notify.assert_called_once_with([self.right])
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(
            watch.unseen_jobs([self.right], watch.load_seen(self.state)), [self.right]
        )


class IncompleteMergeTests(unittest.TestCase):
    def test_false_marker_survives_both_orders_and_all_primary_choices(self):
        partial = job(
            "a", metadata={"source_namespace": "tenant-a", "board_complete": False}
        )
        for healthy_marker in ({}, {"board_complete": True}):
            healthy = job(
                "b",
                provider="workday",
                description="Healthy primary has richer details.",
                metadata={"source_namespace": "tenant-b", **healthy_marker},
            )
            for existing, incoming in ((partial, healthy), (healthy, partial)):
                for primary in (None, partial, healthy):
                    with self.subTest(
                        existing=existing.id,
                        primary=getattr(primary, "id", "automatic"),
                        healthy_marker=healthy_marker,
                    ):
                        merged = merge_jobs(existing, incoming, primary=primary)
                        self.assertIs(merged.metadata["board_complete"], False)
                        self.assertEqual(
                            set(merged.sources), set(partial.sources + healthy.sources)
                        )
                        self.assertIs(partial.metadata["board_complete"], False)
                        self.assertEqual(
                            healthy.metadata.get("board_complete"),
                            healthy_marker.get("board_complete"),
                        )

    def test_marker_survives_transitive_deduplication_in_every_input_order(self):
        shared = "https://careers.example.test/shared"
        partial = job("a", application_url=shared, metadata={"board_complete": False})
        bridge = job("b", application_url=shared, metadata={"source_namespace": "board-b"})
        healthy = job(
            "b",
            application_url="https://new.example.test/b",
            description="Richest details",
            metadata={"source_namespace": "board-b", "board_complete": True},
        )
        for order in itertools.permutations((partial, bridge, healthy)):
            with self.subTest(order=[record.id for record in order]):
                result = deduplicate(order)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].id, healthy.id)
                self.assertIs(result[0].metadata["board_complete"], False)
                self.assertEqual(len(result[0].sources), 3)
                self.assertIs(deduplicate(result)[0].metadata["board_complete"], False)
                self.assertIs(result[0].to_dict()["metadata"]["board_complete"], False)

    def test_healthy_inputs_do_not_acquire_false_marker(self):
        for metadata in ({}, {"board_complete": True}):
            left, right = job("a", metadata=metadata), job("b", metadata=metadata)
            merged = merge_jobs(left, right)
            self.assertEqual(
                merged.metadata.get("board_complete"), metadata.get("board_complete")
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
