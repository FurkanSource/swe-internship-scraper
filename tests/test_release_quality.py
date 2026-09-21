import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from swe_scraper import cli
from swe_scraper.config import load_targets
from swe_scraper.dedupe import PotentialDuplicate
from swe_scraper.health import (
    HealthStatus,
    TargetHealth,
    _failure_category,
    evaluate_target_health,
    run_health_checks,
)
from swe_scraper.models import Job, ScanResult
from swe_scraper.providers.base import Target
from swe_scraper.providers.http import RequestsJsonClient
from swe_scraper.providers.registry import ProviderRegistry
from swe_scraper.scanner import ScanDetails
from swe_scraper.validation import validate_file, validate_payload


def make_job(source_id: str = "1") -> Job:
    return Job(
        id=f"example:{source_id}",
        company="Example",
        title="Software Engineer Intern",
        application_url=f"https://jobs.example.test/{source_id}",
        provider="example",
        source_job_id=source_id,
    )


class FakeResponse:
    def __init__(
        self,
        content: bytes = b'{"ok": true}',
        content_type: str = "application/json",
    ) -> None:
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.encoding = None
        self.raise_error: Exception | None = None

    def raise_for_status(self) -> None:
        if self.raise_error:
            raise self.raise_error

    def json(self):
        return json.loads(self.content)


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response


class HttpClientTests(unittest.TestCase):
    def test_json_and_text_requests_use_bounds_and_headers(self):
        client = RequestsJsonClient(min_host_interval=0, max_response_bytes=100)
        session = FakeSession(FakeResponse())
        with mock.patch.object(client, "_session", return_value=session):
            self.assertEqual(client.get_json("https://example.test/data"), {"ok": True})
            self.assertEqual(
                client.post_json("https://example.test/data", {"x": 1}), {"ok": True}
            )
            session.response = FakeResponse(b"hello", "text/plain")
            self.assertEqual(client.get_text("https://example.test/page"), "hello")
        self.assertEqual(
            session.calls[-1][2]["headers"]["Accept"].split(",")[0], "text/html"
        )

    def test_rejects_oversized_and_non_json_responses(self):
        client = RequestsJsonClient(max_response_bytes=2)
        with self.assertRaisesRegex(ValueError, "exceeded"):
            client._decode(FakeResponse(b"{}x"))
        client = RequestsJsonClient(max_response_bytes=100)
        with self.assertRaisesRegex(ValueError, "expected JSON"):
            client._decode(FakeResponse(b"plain", "text/plain"))
        with self.assertRaisesRegex(ValueError, "exceeded"):
            RequestsJsonClient(max_response_bytes=2)._bounded_content(
                FakeResponse(b"long", "text/plain")
            )

    def test_session_is_thread_local_and_rate_limit_sleeps(self):
        client = RequestsJsonClient(min_host_interval=1)
        session = client._session()
        self.assertIs(client._session(), session)
        with (
            mock.patch("swe_scraper.providers.http.time.monotonic", side_effect=[1.0, 1.2]),
            mock.patch("swe_scraper.providers.http.time.sleep") as sleep,
        ):
            client._next_request.clear()
            client._wait_for_host("https://example.test/one")
            client._wait_for_host("https://example.test/two")
        sleep.assert_called_once_with(0.8)


class HealthExecutionTests(unittest.TestCase):
    def test_all_provider_health_states_and_failure_categories(self):
        checks = [
            TargetHealth("one", "a", HealthStatus.HEALTHY, 1, 0.1),
            TargetHealth("one", "b", HealthStatus.HEALTHY, 1, 0.1),
        ]
        report = evaluate_target_health(checks)
        self.assertEqual(report.status, HealthStatus.HEALTHY)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.to_dict()["status"], "healthy")
        self.assertEqual(evaluate_target_health([]).exit_code, 2)
        with self.assertRaisesRegex(ValueError, "quorum"):
            evaluate_target_health(checks, quorum=0)
        self.assertEqual(
            _failure_category(RuntimeError("pagination stopped")), "pagination"
        )
        self.assertEqual(_failure_category(TimeoutError("timeout")), "timeout")
        self.assertEqual(_failure_category(RuntimeError("HTTP status 500")), "http")
        self.assertEqual(_failure_category(ValueError("bad target")), "contract")
        self.assertEqual(_failure_category(RuntimeError("boom")), "provider")

    def test_runner_isolates_success_empty_and_contract_failure(self):
        class Provider:
            def __init__(self, result):
                self.result = result

            def validate_target(self, target):
                if self.result == "invalid":
                    raise ValueError("bad target")

            def fetch(self, target, client):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

        targets = [
            Target("ok", "ok-a", "a"),
            Target("ok", "ok-b", "b"),
            Target("empty", "empty", "c"),
            Target("invalid", "invalid", "d"),
        ]

        def provider(name):
            if name == "ok":
                return Provider([make_job(name)])
            if name == "empty":
                return Provider([])
            return Provider(name)

        with mock.patch("swe_scraper.health.get_provider", side_effect=provider):
            report = run_health_checks(targets, client=mock.Mock(), max_workers=4)
        self.assertEqual(report.status, HealthStatus.UNHEALTHY)
        categories = {check.target: check.category for check in report.checks}
        self.assertEqual(categories["empty"], "empty")
        self.assertEqual(categories["invalid"], "contract")


class RegistryConfigValidationTests(unittest.TestCase):
    def test_registry_discovery_description_and_errors(self):
        class Extension:
            name = "extension"
            required_options = ("origin",)

            def validate_target(self, target):
                return None

            def fetch(self, target, client):
                return []

        class EmptyExtension(Extension):
            name = ""

        entry = mock.Mock()
        entry.load.return_value = Extension
        entry_points = mock.Mock()
        entry_points.select.return_value = [entry]
        with mock.patch(
            "swe_scraper.providers.registry.metadata.entry_points",
            return_value=entry_points,
        ):
            registry = ProviderRegistry()
        self.assertEqual(registry.names, ("extension",))
        self.assertEqual(registry.describe()[0]["required_options"], ["origin"])
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(Extension())
        with self.assertRaisesRegex(ValueError, "non-empty"):
            registry.register(EmptyExtension())
        with self.assertRaisesRegex(ValueError, "unsupported"):
            registry.get("missing")

    def test_registry_can_defer_entry_point_discovery(self):
        entry_points = mock.Mock()
        entry_points.select.return_value = []
        registry = ProviderRegistry(discover=True, defer_discovery=True)
        with (
            mock.patch(
                "swe_scraper.providers.registry.metadata.entry_points",
                return_value=entry_points,
            ),
            mock.patch.object(registry, "discover", wraps=registry.discover) as discover,
        ):
            self.assertEqual(registry.names, ())
        discover.assert_called_once_with()

    def test_target_loading_and_validation_failure_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(
                json.dumps(
                    {
                        "greenhouse": [
                            {"name": "One", "slug": "one", "profiles": ["all"]},
                            {"name": "Two", "slug": "two", "profiles": ["priority"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(len(load_targets(path, profile="all")), 2)
            self.assertEqual(len(load_targets(path, profile="priority")), 1)
            with self.assertRaisesRegex(ValueError, "target profile must"):
                load_targets(path, profile="missing")
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                load_targets(path)

        self.assertFalse(validate_payload("bad").valid)
        self.assertFalse(validate_payload({}).valid)
        self.assertFalse(validate_payload(["bad"]).valid)
        self.assertFalse(validate_payload([{"id": "missing"}]).valid)
        self.assertFalse(validate_file("does-not-exist.json").valid)

    def test_cli_provider_inventory_and_validate_commands(self):
        with mock.patch("sys.stdout") as stdout:
            self.assertEqual(cli.main(["providers", "--json"]), 0)
            self.assertTrue(stdout.write.called)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            path.write_text(json.dumps(ScanResult.from_iterables([make_job()]).to_dict()))
            self.assertEqual(cli.main(["validate", str(path)]), 0)
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(cli.main(["validate", str(path)]), 1)

    def test_cli_writes_deduplication_and_health_reports(self):
        details = ScanDetails(
            ScanResult.from_iterables([make_job()]),
            (PotentialDuplicate("left", "right", "similar", 0.8),),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dedupe_path = root / "dedupe.json"
            args = mock.Mock(
                providers=None,
                targets=None,
                target_set="priority",
                max_workers=1,
                all_jobs=True,
                location=[],
                include=[],
                exclude=[],
                dedupe_report=dedupe_path,
            )
            with (
                mock.patch("swe_scraper.cli.load_targets", return_value=[]),
                mock.patch("swe_scraper.cli.scan_targets_detailed", return_value=details),
            ):
                result = cli._run_scan(args)
            self.assertEqual(result.jobs[0].source_job_id, "1")
            self.assertEqual(
                json.loads(dedupe_path.read_text(encoding="utf-8"))["candidate_count"],
                1,
            )

            health_path = root / "health.json"
            report = evaluate_target_health(
                [
                    TargetHealth("one", "a", HealthStatus.HEALTHY, 1, 0.1),
                    TargetHealth("one", "b", HealthStatus.HEALTHY, 1, 0.1),
                ]
            )
            health_args = mock.Mock(
                targets=None,
                providers=None,
                max_workers=2,
                quorum=2,
                output=health_path,
            )
            with (
                mock.patch("swe_scraper.cli.load_targets", return_value=[]),
                mock.patch("swe_scraper.cli.run_health_checks", return_value=report),
            ):
                self.assertEqual(cli._health_command(health_args), 0)
            self.assertEqual(
                json.loads(health_path.read_text(encoding="utf-8"))["status"],
                "healthy",
            )

        self.assertEqual(
            cli._csv_values(["greenhouse, lever", "ashby"]),
            [
                "greenhouse",
                "lever",
                "ashby",
            ],
        )
        with mock.patch("sys.stdout") as stdout:
            self.assertEqual(cli.main(["providers"]), 0)
            self.assertTrue(stdout.write.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
