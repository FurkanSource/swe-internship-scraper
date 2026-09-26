"""Offline behavioral checks for bounded provider pagination and detail fetches."""

import threading
import unittest
import urllib.parse
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.providers.base import Target
from swe_scraper.providers.http import RequestsJsonClient
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider

PROVIDERS = (SmartRecruitersProvider, OracleProvider)


def target_for(provider, **options):
    if provider.name == "oracle":
        options["origin"] = "https://example.test"
    return Target(provider.name, "Example", "example", options)


def page_for(provider, ids, total, has_more=False):
    if provider.name == "smartrecruiters":
        return {"content": [{"id": value} for value in ids], "totalFound": total}
    return {
        "items": [
            {
                "TotalJobsCount": total,
                "requisitionList": {
                    "items": [{"Id": value, "Title": f"Role {value}"} for value in ids],
                    "hasMore": has_more,
                },
            }
        ]
    }


def detail_for(provider, source_id):
    if provider.name == "smartrecruiters":
        return {"id": source_id, "name": f"Role {source_id}"}
    return {"items": [{"Id": source_id, "Title": f"Role {source_id}"}]}


class RoutingClient:
    def __init__(self, provider, page, detail=None):
        self.provider = provider
        self.page = page
        self.detail = detail or (lambda source_id: detail_for(provider, source_id))
        self.offsets = []
        self.detail_ids = []
        self.lock = threading.Lock()

    def get_json(self, url, **kwargs):
        if self.provider.name == "smartrecruiters":
            is_page = url.endswith("/postings")
            key = (
                kwargs["params"]["offset"]
                if is_page
                else urllib.parse.unquote(url.rsplit("/", 1)[1])
            )
        else:
            is_page = "recruitingCEJobRequisitions?" in url
            finder = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["finder"][0]
            key = int(finder.rsplit("offset=", 1)[1]) if is_page else finder.split('"')[1]
        with self.lock:
            (self.offsets if is_page else self.detail_ids).append(key)
        return self.page(key) if is_page else self.detail(key)


class ProviderReliabilityTests(unittest.TestCase):
    def test_detail_overlap_cap_and_stable_order(self):
        for provider_type in PROVIDERS:
            for configured in (None, 1, "2", 8):
                with self.subTest(provider=provider_type.name, workers=configured):
                    provider = provider_type()
                    workers = 4 if configured is None else int(configured)
                    options = {} if configured is None else {"detail_workers": configured}
                    ids = [str(index) for index in range(workers * 2)]
                    barrier = threading.Barrier(workers)
                    gates = [threading.Event() for _ in ids]
                    for index in range(workers - 1, len(ids), workers):
                        gates[index].set()
                    lock = threading.Lock()
                    active = 0
                    peak = 0
                    finished = []
                    caller = threading.get_ident()

                    def detail(
                        source_id,
                        lock=lock,
                        workers=workers,
                        barrier=barrier,
                        gates=gates,
                        caller=caller,
                        finished=finished,
                        provider=provider,
                    ):
                        nonlocal active, peak
                        index = int(source_id)
                        with lock:
                            active += 1
                            peak = max(peak, active)
                            self.assertLessEqual(active, workers)
                        try:
                            # Timeouts only guard deadlocks; events determine ordering.
                            barrier.wait(timeout=5)
                            self.assertTrue(gates[index].wait(timeout=5))
                            if workers == 1:
                                self.assertEqual(threading.get_ident(), caller)
                            with lock:
                                finished.append(source_id)
                            if index % workers:
                                gates[index - 1].set()
                            return detail_for(provider, source_id)
                        finally:
                            with lock:
                                active -= 1

                    client = RoutingClient(
                        provider,
                        lambda _offset, provider=provider, ids=ids: page_for(
                            provider, ids, len(ids)
                        ),
                        detail,
                    )
                    jobs = provider.fetch(target_for(provider, **options), client)
                    self.assertEqual(peak, workers)
                    self.assertEqual(active, 0)
                    self.assertEqual([job.source_job_id for job in jobs], ids)
                    self.assertEqual(
                        [job.title for job in jobs], [f"Role {i}" for i in ids]
                    )
                    self.assertEqual(
                        finished,
                        [
                            value
                            for start in range(0, len(ids), workers)
                            for value in reversed(ids[start : start + workers])
                        ],
                    )
                    self.assertCountEqual(client.detail_ids, ids)

    def test_invalid_workers_fail_before_any_request(self):
        for provider_type in PROVIDERS:
            for value in (0, 9, -1, True, False, 1.5, "2.5", "bad", None, [], {}):
                with self.subTest(provider=provider_type.name, workers=value):
                    provider = provider_type()
                    target = target_for(provider, detail_workers=value)
                    client = RoutingClient(
                        provider,
                        lambda _offset, provider=provider: page_for(provider, [], 0),
                    )
                    with self.assertRaisesRegex(ValueError, "detail_workers"):
                        provider.validate_target(target)
                    with self.assertRaisesRegex(ValueError, "detail_workers"):
                        provider.fetch(target, client)
                    self.assertEqual(client.offsets, [])
                    self.assertEqual(client.detail_ids, [])

    def test_total_drift_restarts_cleanly_for_growth_and_shrinkage(self):
        for provider_type in PROVIDERS:
            for first_total, changed_total in ((3, 5), (5, 3)):
                with self.subTest(provider=provider_type.name, first_total=first_total):
                    provider = provider_type()
                    pages = iter(
                        [
                            page_for(provider, ["stale", "shared"], first_total, True),
                            page_for(provider, ["transient"], changed_total, True),
                            page_for(provider, ["shared", "new"], 3, True),
                            page_for(provider, ["last"], 3),
                        ]
                    )
                    client = RoutingClient(
                        provider, lambda _offset, pages=pages: next(pages)
                    )
                    jobs = provider.fetch(target_for(provider, max_pages=2), client)
                    self.assertEqual(client.offsets, [0, 2, 0, 2])
                    self.assertEqual(
                        [job.source_job_id for job in jobs], ["shared", "new", "last"]
                    )
                    self.assertCountEqual(client.detail_ids, ["shared", "new", "last"])

    def test_total_drift_can_restart_to_an_empty_board(self):
        for provider_type in PROVIDERS:
            with self.subTest(provider=provider_type.name):
                provider = provider_type()
                pages = iter(
                    [
                        page_for(provider, ["stale"], 2, True),
                        page_for(provider, [], 0),
                        page_for(provider, [], 0),
                    ]
                )
                client = RoutingClient(provider, lambda _offset, pages=pages: next(pages))
                self.assertEqual(provider.fetch(target_for(provider), client), [])
                self.assertEqual(client.offsets, [0, 1, 0])
                self.assertEqual(client.detail_ids, [])

    def test_repeated_total_drift_fails_without_a_third_attempt(self):
        for provider_type in PROVIDERS:
            with self.subTest(provider=provider_type.name):
                provider = provider_type()
                pages = iter(
                    page_for(provider, [str(total)], total, True) for total in (2, 3, 4, 5)
                )
                client = RoutingClient(provider, lambda _offset, pages=pages: next(pages))
                with self.assertRaisesRegex(RuntimeError, "changed .*during pagination"):
                    provider.fetch(target_for(provider, max_pages=2), client)
                self.assertEqual(client.offsets, [0, 1, 0, 1])
                self.assertEqual(client.detail_ids, [])

    def test_restart_still_obeys_pagination_limit(self):
        for provider_type in PROVIDERS:
            with self.subTest(provider=provider_type.name):
                provider = provider_type()
                pages = iter(
                    page_for(provider, [str(index)], total, True)
                    for index, total in enumerate((2, 3, 5, 5))
                )
                client = RoutingClient(provider, lambda _offset, pages=pages: next(pages))
                with self.assertRaisesRegex(RuntimeError, "pagination limit"):
                    provider.fetch(target_for(provider, max_pages=2), client)
                self.assertEqual(client.offsets, [0, 1, 0, 1])
                self.assertEqual(client.detail_ids, [])

    def test_malformed_page_does_not_restart(self):
        for provider_type in PROVIDERS:
            with self.subTest(provider=provider_type.name):
                provider = provider_type()
                pages = iter([page_for(provider, ["one"], 2, True), {}])
                client = RoutingClient(provider, lambda _offset, pages=pages: next(pages))
                with self.assertRaisesRegex(RuntimeError, "malformed"):
                    provider.fetch(target_for(provider), client)
                self.assertEqual(client.offsets, [0, 1])
                self.assertEqual(client.detail_ids, [])

    def test_malformed_detail_fails_the_whole_board_without_retry(self):
        for provider_type in PROVIDERS:
            for malformed in (None, {}, {"items": [None]}, {"id": "bad"}):
                with self.subTest(provider=provider_type.name, malformed=malformed):
                    provider = provider_type()
                    client = RoutingClient(
                        provider,
                        lambda _offset, provider=provider: page_for(
                            provider, ["good", "bad"], 2
                        ),
                        lambda source_id, provider=provider, malformed=malformed: (
                            malformed
                            if source_id == "bad"
                            else detail_for(provider, source_id)
                        ),
                    )
                    with self.assertRaisesRegex(RuntimeError, "malformed details"):
                        provider.fetch(target_for(provider), client)
                    self.assertEqual(client.offsets, [0])
                    self.assertEqual(client.detail_ids.count("bad"), 1)

    def test_transport_errors_do_not_restart_even_when_message_mentions_drift(self):
        for provider_type in PROVIDERS:
            for phase in ("page", "detail"):
                with self.subTest(provider=provider_type.name, phase=phase):
                    provider = provider_type()
                    error = RuntimeError(
                        "changed totalFound TotalJobsCount during pagination"
                    )

                    def fail(_key, error=error):
                        raise error

                    client = RoutingClient(
                        provider,
                        fail
                        if phase == "page"
                        else lambda _offset, provider=provider: page_for(
                            provider, ["one"], 1
                        ),
                        fail if phase == "detail" else None,
                    )
                    with self.assertRaises(RuntimeError) as raised:
                        provider.fetch(target_for(provider), client)
                    self.assertIs(raised.exception, error)
                    self.assertEqual(client.offsets, [0])
                    self.assertEqual(
                        client.detail_ids, ["one"] if phase == "detail" else []
                    )

    def test_workers_use_the_supplied_clients_shared_host_limiter(self):
        for provider_type in PROVIDERS:
            with self.subTest(provider=provider_type.name):
                provider = provider_type()
                ids = [str(index) for index in range(8)]
                routes = RoutingClient(
                    provider,
                    lambda _offset, provider=provider, ids=ids: page_for(
                        provider, ids, len(ids)
                    ),
                )
                session = mock.Mock()
                session.get.side_effect = routes.get_json
                client = RequestsJsonClient(min_host_interval=0.25)
                with (
                    mock.patch.object(RequestsJsonClient, "_session", return_value=session),
                    mock.patch.object(
                        RequestsJsonClient, "_decode", side_effect=lambda value: value
                    ),
                    mock.patch(
                        "swe_scraper.providers.http.time.monotonic", return_value=100.0
                    ),
                    mock.patch("swe_scraper.providers.http.time.sleep") as sleep,
                ):
                    jobs = provider.fetch(target_for(provider), client)
                self.assertEqual([job.source_job_id for job in jobs], ids)
                self.assertEqual(session.get.call_count, 9)
                self.assertEqual(
                    sorted(call.args[0] for call in sleep.call_args_list),
                    [0.25 * index for index in range(1, 9)],
                )
                self.assertEqual(list(client._next_request.values()), [102.25])


if __name__ == "__main__":
    unittest.main(verbosity=2)
