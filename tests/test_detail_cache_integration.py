"""Cached details must never replace fresh, complete listing discovery."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper import cli
from swe_scraper.detail_cache import DetailCache
from swe_scraper.providers.base import Target
from swe_scraper.providers.http import RequestsJsonClient
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider


class BoardClient(RequestsJsonClient):
    def __init__(self, cache, refresh=False):
        super().__init__(detail_cache=cache, refresh_cache=refresh)
        self.rows = [{"id": "1", "name": "Software Engineer Intern", "version": 1}]
        self.list_calls = 0
        self.network_details = 0
        self.fail = False

    def get_json(self, url, **kwargs):
        if self.fail:
            raise RuntimeError("upstream unavailable")
        if url.endswith("/postings"):
            self.list_calls += 1
            return {"totalFound": len(self.rows), "content": self.rows}
        self.network_details += 1
        return {"id": "1", "name": "Software Engineer Intern"}


class DetailCacheIntegrationTests(unittest.TestCase):
    def test_oracle_detail_title_alias_works_with_default_transport(self):
        target = Target(
            "oracle", "Example", "CX_1", {"origin": "https://example.oraclecloud.com"}
        )
        for enabled in (False, True):
            with self.subTest(cache=enabled), tempfile.TemporaryDirectory() as directory:
                cache = DetailCache(Path(directory)) if enabled else None
                client = RequestsJsonClient(detail_cache=cache)
                listing = {"items": [{"Id": "1"}], "hasMore": False}
                detail = {
                    "items": [{"Id": "1", "RequisitionTitle": "Software Engineer Intern"}]
                }
                with mock.patch.object(client, "get_json", side_effect=[listing, detail]):
                    jobs = OracleProvider().fetch_candidates(target, client)
                self.assertEqual(jobs[0].title, "Software Engineer Intern")
                if enabled:
                    with mock.patch.object(
                        client, "get_json", side_effect=[listing]
                    ) as get:
                        second = OracleProvider().fetch_candidates(target, client)
                    self.assertEqual(second[0].id, jobs[0].id)
                    self.assertTrue(second[0].metadata["detail_cached"])
                    self.assertEqual(get.call_count, 1)

    def test_fresh_lists_cache_hits_changed_rows_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            client = BoardClient(DetailCache(Path(directory)))
            provider = SmartRecruitersProvider()
            target = Target("smartrecruiters", "Example", "example", {"detail_workers": 1})
            first = provider.fetch_candidates(target, client)
            second = provider.fetch_candidates(target, client)
            self.assertEqual([job.id for job in first], [job.id for job in second])
            self.assertEqual((client.list_calls, client.network_details), (2, 1))
            self.assertTrue(second[0].metadata["detail_cached"])
            self.assertEqual(
                first[0].metadata["detail_fetched_at"],
                second[0].metadata["detail_fetched_at"],
            )
            client.rows[0]["version"] = 2
            provider.fetch_candidates(target, client)
            self.assertEqual(client.network_details, 2)
            client.rows = []
            self.assertEqual(provider.fetch_candidates(target, client), [])
            client.fail = True
            with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
                provider.fetch_candidates(target, client)

    def test_force_refresh_updates_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            client = BoardClient(DetailCache(Path(directory)))
            provider = SmartRecruitersProvider()
            target = Target("smartrecruiters", "Example", "example", {"detail_workers": 1})
            provider.fetch(target, client)
            client.refresh_cache = True
            provider.fetch(target, client)
            self.assertEqual(client.network_details, 2)
            client.refresh_cache = False
            provider.fetch(target, client)
            self.assertEqual(client.network_details, 2)

    def test_expired_cache_never_hides_detail_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            client = BoardClient(DetailCache(Path(directory)))
            client.get_detail_json("https://example.test/detail", {"id": 1})
            with mock.patch.object(client.detail_cache, "load", return_value=None):
                client.fail = True
                with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
                    client.get_detail_json("https://example.test/detail", {"id": 1})

    def test_cache_options_fail_before_scanning_and_health_has_no_cache_flag(self):
        with mock.patch("swe_scraper.cli.load_targets") as load:
            with self.assertRaises(SystemExit):
                cli.main(["scan", "--cache-ttl", "3601"])
            load.assert_not_called()

    def test_invalid_detail_payload_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = DetailCache(Path(directory))
            client = RequestsJsonClient(detail_cache=cache)
            with (
                mock.patch.object(client, "get_json", return_value={}),
                mock.patch.object(cache, "save") as save,
            ):
                with self.assertRaisesRegex(RuntimeError, "malformed details"):
                    client.get_detail_json(
                        "https://example.test/detail",
                        {"id": 1},
                        validate=lambda data: "id" in data,
                    )
                save.assert_not_called()
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["scan"]).cache_ttl, 0)
        self.assertFalse(hasattr(parser.parse_args(["health"]), "cache_ttl"))
        with mock.patch("swe_scraper.cli.load_targets") as load:
            with self.assertRaises(SystemExit):
                cli.main(["scan", "--target-set", "all", "--resume", "--refresh"])
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
