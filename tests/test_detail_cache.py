"""Behavior contracts for the isolated public-detail JSON cache."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import UUID

from _bootstrap import ROOT  # noqa: F401

from swe_scraper import detail_cache
from swe_scraper.detail_cache import DetailCache, DetailCacheEntry


class DetailCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "details"
        self.instant = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        self.cache = DetailCache(self.root, ttl_seconds=30, now=lambda: self.instant)
        self.key: dict[str, Any] = {
            "provider": "example",
            "listing_token": {"id": "1", "title": "Software Intern"},
            "url": "https://example.test/jobs/1",
            "options": {"locale": "en-US"},
        }

    def entry_path(self) -> Path:
        paths = list(self.root.glob("*.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def edit_record(self, **changes: object) -> None:
        path = self.entry_path()
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(changes)
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_missing_load_does_not_create_directory(self) -> None:
        self.assertIsNone(self.cache.load(self.key))
        self.assertFalse(self.root.exists())

    def test_round_trip_disk_reuse_and_original_fetch_time(self) -> None:
        payload = {"description": "Build tools", "id": "1"}
        saved = self.cache.save(self.key, payload)
        self.assertIsInstance(saved, DetailCacheEntry)
        self.assertEqual(saved.payload, payload)
        self.assertEqual(saved.fetched_at, self.instant.isoformat())
        self.instant += timedelta(seconds=5)
        other = DetailCache(self.root, ttl_seconds=30, now=lambda: self.instant)
        loaded = other.load(self.key)
        self.assertEqual(loaded, saved)
        self.assertEqual(self.cache.load(self.key), saved)
        assert loaded is not None
        self.assertEqual(loaded.fetched_at, "2026-09-28T12:00:00+00:00")

    def test_ttl_exact_boundary_and_no_stale_fallback(self) -> None:
        self.cache.save(self.key, {"id": "1"})
        self.instant += timedelta(seconds=29, microseconds=999999)
        self.assertIsNotNone(self.cache.load(self.key))
        self.instant += timedelta(microseconds=1)
        self.assertIsNone(self.cache.load(self.key))
        with mock.patch.object(Path, "open", side_effect=OSError("unavailable")):
            self.assertIsNone(self.cache.load(self.key))
        self.instant += timedelta(days=1)
        self.assertIsNone(self.cache.load(self.key))

    def test_valid_ttl_endpoints_and_invalid_values(self) -> None:
        for value in (1, 3600):
            with self.subTest(value=value):
                DetailCache(self.root, ttl_seconds=value)
        for invalid_value in (0, -1, 3601, True, 1.5, "30", None):
            with self.subTest(value=invalid_value), self.assertRaises(ValueError):
                DetailCache(self.root, ttl_seconds=invalid_value)  # type: ignore[arg-type]

    def test_canonical_key_order_and_unicode(self) -> None:
        saved = self.cache.save({"b": [1, "Ã©"], "a": {"y": 2, "x": 1}}, {"Ã©": "âœ“"})
        equivalent = {"a": {"x": 1, "y": 2}, "b": [1, "Ã©"]}
        self.assertEqual(self.cache.load(equivalent), saved)

    def test_listing_url_provider_and_options_changes_are_misses(self) -> None:
        self.cache.save(self.key, {"id": "1"})
        variants = [
            {**self.key, "listing_token": {"id": "1", "title": "Updated"}},
            {**self.key, "url": "https://other.test/jobs/1"},
            {**self.key, "provider": "other"},
            {**self.key, "options": {"locale": "fr"}},
        ]
        for key in variants:
            with self.subTest(key=key):
                self.assertIsNone(self.cache.load(key))

    def test_nested_payload_mutation_does_not_change_saved_snapshot(self) -> None:
        payload = {"nested": {"description": "original"}}
        saved = self.cache.save(self.key, payload)
        payload["nested"]["description"] = "caller changed"
        self.assertEqual(saved.payload, {"nested": {"description": "original"}})
        loaded = self.cache.load(self.key)
        assert loaded is not None
        loaded.payload["nested"]["description"] = "reader changed"
        self.assertEqual(self.cache.load(self.key), saved)

    def test_version_changes_namespace_and_stored_version_mismatch_is_miss(self) -> None:
        self.cache.save(self.key, {"id": "1"})
        with mock.patch.object(detail_cache, "__version__", "next-version"):
            self.assertIsNone(self.cache.load(self.key))
            self.cache.save(self.key, {"id": "new"})
        self.assertEqual(len(list(self.root.glob("*.json"))), 2)
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            record["version"] = "wrong"
            path.write_text(json.dumps(record), encoding="utf-8")
        self.assertIsNone(self.cache.load(self.key))

    def test_payload_sha_matches_canonical_json_and_detects_tampering(self) -> None:
        payload = {"z": ["Ã©"], "a": 1}
        self.cache.save(self.key, payload)
        record = json.loads(self.entry_path().read_text(encoding="utf-8"))
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(record["payload_sha256"], hashlib.sha256(encoded).hexdigest())
        self.edit_record(payload={"z": ["changed"], "a": 1})
        self.assertIsNone(self.cache.load(self.key))

    def test_corrupt_records_are_misses(self) -> None:
        for content in ("{broken", "[]", "null", "42", '{"payload": {}}', "NaN"):
            with self.subTest(content=content):
                self.cache.save(self.key, {"id": "1"})
                self.entry_path().write_text(content, encoding="utf-8")
                self.assertIsNone(self.cache.load(self.key))
        self.entry_path().write_bytes(b"\xff")
        self.assertIsNone(self.cache.load(self.key))

    def test_missing_or_wrong_metadata_is_miss(self) -> None:
        for changes in (
            {"payload_sha256": None},
            {"key_sha256": "wrong"},
            {"schema_version": -1},
            {"schema_version": True},
            {"fetched_at": None},
        ):
            with self.subTest(changes=changes):
                self.cache.save(self.key, {"id": "1"})
                self.edit_record(**changes)
                self.assertIsNone(self.cache.load(self.key))
        self.cache.save(self.key, {"id": "1"})
        path = self.entry_path()
        record = json.loads(path.read_text(encoding="utf-8"))
        del record["payload"]
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertIsNone(self.cache.load(self.key))

    def test_invalid_naive_future_and_non_utc_disk_timestamps_miss(self) -> None:
        for timestamp in (
            "not-a-date",
            "2026-09-28T12:00:00",
            "2026-09-28T12:00:01+00:00",
            "2026-09-28T08:00:00-04:00",
        ):
            with self.subTest(timestamp=timestamp):
                self.cache.save(self.key, {"id": "1"})
                self.edit_record(fetched_at=timestamp)
                self.assertIsNone(self.cache.load(self.key))

    def test_clock_normalizes_to_utc_and_rejects_naive_clock(self) -> None:
        offset = timezone(timedelta(hours=-4))
        self.instant = datetime(2026, 9, 28, 8, tzinfo=offset)
        entry = self.cache.save(self.key, {})
        self.assertEqual(entry.fetched_at, "2026-09-28T12:00:00+00:00")
        self.instant = datetime(2026, 9, 28, 12)
        with self.assertRaises(ValueError):
            self.cache.save(self.key, {})
        self.assertIsNone(self.cache.load(self.key))

    def test_save_refreshes_timestamp_without_hit_refresh(self) -> None:
        first = self.cache.save(self.key, {"description": "old"})
        self.instant += timedelta(seconds=10)
        self.assertEqual(self.cache.load(self.key), first)
        second = self.cache.save(self.key, {"description": "new"})
        self.assertEqual(second.fetched_at, self.instant.isoformat())
        self.assertEqual(self.cache.load(self.key), second)

    def test_atomic_replace_failure_keeps_old_entry_and_cleans_temp(self) -> None:
        first = self.cache.save(self.key, {"description": "old"})
        self.instant += timedelta(seconds=10)
        with mock.patch(
            "swe_scraper.detail_cache.os.replace", side_effect=OSError("disk full")
        ):
            fresh = self.cache.save(self.key, {"description": "fresh"})
        self.assertEqual(fresh.payload, {"description": "fresh"})
        self.assertEqual(fresh.fetched_at, self.instant.isoformat())
        self.assertEqual(self.cache.load(self.key), first)
        self.assertEqual(list(self.root.iterdir()), [self.entry_path()])

    def test_atomic_replace_uses_unique_temp_in_same_directory(self) -> None:
        replace = os.replace
        temporary_paths: list[Path] = []

        def observe(source: Path, destination: Path) -> None:
            source = Path(source)
            destination = Path(destination)
            self.assertEqual(source.parent, destination.parent)
            self.assertTrue(source.exists())
            UUID(source.name.split(".")[-2])
            temporary_paths.append(source)
            replace(source, destination)

        with mock.patch("swe_scraper.detail_cache.os.replace", side_effect=observe):
            self.cache.save(self.key, {"id": "1"})
            self.cache.save(self.key, {"id": "2"})
        self.assertEqual(len(set(temporary_paths)), 2)
        self.assertFalse(any(path.exists() for path in temporary_paths))

    def test_unwritable_or_file_root_returns_fresh_entry_without_caching(self) -> None:
        self.root.write_text("blocker", encoding="utf-8")
        saved = self.cache.save(self.key, {"id": "1"})
        self.assertEqual(saved.payload, {"id": "1"})
        self.assertIsNone(self.cache.load(self.key))

    def test_write_failure_returns_fresh_entry_and_removes_temp(self) -> None:
        with mock.patch.object(Path, "open", side_effect=OSError("write denied")):
            entry = self.cache.save(self.key, {})
        self.assertEqual(entry.payload, {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_json_null_and_empty_payloads_are_hits(self) -> None:
        payloads: tuple[object, ...] = (None, {}, [], "", False, 0)
        for payload in payloads:
            with self.subTest(payload=payload):
                saved = self.cache.save(self.key, payload)
                self.assertEqual(self.cache.load(self.key), saved)

    def test_non_json_keys_fail_open_and_invalid_payloads_raise(self) -> None:
        for key in (object(), {"value": float("nan")}):
            with self.subTest(key=key):
                self.assertIsNone(self.cache.load(key))
                self.assertEqual(self.cache.save(key, {}).payload, {})
        self.assertFalse(self.root.exists())
        for payload in (object(), {"value": float("nan")}, {"value": float("inf")}):
            with self.subTest(payload=payload), self.assertRaises((TypeError, ValueError)):
                self.cache.save(self.key, payload)

    def test_entry_size_limit_includes_envelope_and_encoded_bytes(self) -> None:
        with mock.patch.object(detail_cache, "MAX_ENTRY_BYTES", 1024):
            self.cache.save(self.key, {"description": "x" * 700})
            path = self.entry_path()
            self.assertLessEqual(path.stat().st_size, 1024)
            path.unlink()
            returned = self.cache.save(self.key, {"description": "x" * 900})
            self.assertEqual(returned.payload, {"description": "x" * 900})
            self.assertEqual(list(self.root.iterdir()), [])
            self.cache.save(self.key, {"description": "Ã©" * 450})
            self.assertEqual(list(self.root.iterdir()), [])

    def test_exact_entry_byte_boundary_and_default_limits(self) -> None:
        self.assertEqual(detail_cache.MAX_ENTRY_BYTES, 10 * 1024 * 1024)
        self.assertEqual(detail_cache.MAX_CACHE_BYTES, 128 * 1024 * 1024)
        self.assertEqual(detail_cache.MAX_CACHE_ENTRIES, 4096)
        with mock.patch.object(detail_cache, "MAX_ENTRY_BYTES", 1024):
            self.cache.save(self.key, {"description": ""})
            path = self.entry_path()
            remaining = 1024 - path.stat().st_size
            self.cache.save(self.key, {"description": "x" * remaining})
            self.assertEqual(path.stat().st_size, 1024)
            self.assertIsNotNone(self.cache.load(self.key))
            path.unlink()
            self.cache.save(self.key, {"description": "x" * (remaining + 1)})
            self.assertIsNone(self.cache.load(self.key))

    def test_oversized_file_read_is_bounded(self) -> None:
        self.cache.save(self.key, {})
        path = self.entry_path()
        path.write_bytes(b"x" * 1025)
        original_open = Path.open
        reads: list[int] = []

        class Reader:
            def __enter__(self) -> Reader:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int) -> bytes:
                reads.append(size)
                return b"x" * size

        def tracked_open(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path == self.entry_path() and args == ("rb",):
                return Reader()
            return original_open(path, *args, **kwargs)

        with (
            mock.patch.object(detail_cache, "MAX_ENTRY_BYTES", 1024),
            mock.patch.object(Path, "open", tracked_open),
        ):
            self.assertIsNone(self.cache.load(self.key))
        self.assertEqual(reads, [1025])

    def test_entry_count_eviction_preserves_newest_and_unrelated_files(self) -> None:
        self.root.mkdir()
        unrelated = self.root / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")
        with mock.patch.object(detail_cache, "MAX_CACHE_ENTRIES", 2):
            for index in range(3):
                self.cache.save({"id": index}, {"id": index})
                for path in self.root.glob("*.json"):
                    record = json.loads(path.read_text(encoding="utf-8"))
                    stamp = 100 + record["payload"]["id"]
                    os.utime(path, (stamp, stamp))
        self.assertEqual(len(list(self.root.glob("*.json"))), 2)
        self.assertIsNone(self.cache.load({"id": 0}))
        self.assertIsNotNone(self.cache.load({"id": 1}))
        self.assertIsNotNone(self.cache.load({"id": 2}))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_total_byte_eviction(self) -> None:
        with mock.patch.object(detail_cache, "MAX_CACHE_BYTES", 1024):
            for index in range(4):
                self.cache.save({"id": index}, {"description": "x" * 250})
                total = sum(path.stat().st_size for path in self.root.glob("*.json"))
                self.assertLessEqual(total, 1024)
        self.assertIsNone(self.cache.load({"id": 0}))
        self.assertIsNotNone(self.cache.load({"id": 3}))

    def test_key_is_not_stored_and_filename_cannot_escape_root(self) -> None:
        marker = "../private-listing-token"
        self.cache.save({"listing_token": marker}, {"description": "public"})
        path = self.entry_path()
        self.assertRegex(path.name, r"^[0-9a-f]{64}\.json$")
        self.assertNotIn(marker, path.read_text(encoding="utf-8"))
        self.assertEqual(path.parent, self.root)

    def test_default_os_cache_directories_without_creation(self) -> None:
        for platform, env, expected in (
            ("win32", {"LOCALAPPDATA": str(self.root)}, self.root),
            ("linux", {"XDG_CACHE_HOME": str(self.root)}, self.root),
            ("darwin", {}, Path(self.directory.name) / "Library" / "Caches"),
        ):
            with (
                self.subTest(platform=platform),
                mock.patch("swe_scraper.detail_cache.sys.platform", platform),
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(Path, "home", return_value=Path(self.directory.name)),
            ):
                cache = DetailCache()
                self.assertEqual(cache.root, expected / "swe-scraper" / "details")
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
