"""CLI recovery defaults, truthful status, and bounded failure diagnostics."""

import argparse
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from swe_scraper import cli
from swe_scraper.checkpoints import ScanCheckpointStore
from swe_scraper.models import Job, ProviderFailure, ScanResult
from swe_scraper.providers.base import Target
from swe_scraper.scanner import ScanDetails

POLICIES = (
    ("scan", [], True),
    ("scan", ["--allow-partial"], True),
    ("scan", ["--complete-boards-only"], False),
    ("watch", [], False),
    ("watch", ["--allow-partial"], True),
    ("watch", ["--complete-boards-only"], False),
)


def sample_job():
    return Job(
        id="greenhouse:acme:1",
        company="Acme",
        title="Software Engineering Intern",
        application_url="https://job-boards.greenhouse.io/acme/jobs/1",
        provider="greenhouse",
        source_job_id="1",
    )


def failure():
    return ProviderFailure("greenhouse", "Broken", "broken", "board unavailable")


class CliRecoveryPolicyTests(unittest.TestCase):
    def test_parser_uses_one_tristate_store_const_destination(self):
        parser = cli.build_parser()
        commands = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ).choices
        for command, flags, expected in POLICIES:
            with self.subTest(command=command, flags=flags):
                args = parser.parse_args([command, *flags])
                self.assertIs(args.allow_partial, expected if flags else None)
                self.assertFalse(hasattr(args, "complete_boards_only"))
                for flag, value in (
                    ("--allow-partial", True),
                    ("--complete-boards-only", False),
                ):
                    action = next(
                        item
                        for item in commands[command]._actions
                        if flag in item.option_strings
                    )
                    self.assertIsInstance(action, argparse._StoreConstAction)
                    self.assertEqual(action.dest, "allow_partial")
                    self.assertIs(action.const, value)
                    self.assertIsNone(action.default)

    def test_conflicting_recovery_flags_rejected_in_both_orders(self):
        for command in ("scan", "watch"):
            for flags in (
                ["--allow-partial", "--complete-boards-only"],
                ["--complete-boards-only", "--allow-partial"],
            ):
                with (
                    self.subTest(command=command, flags=flags),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli.build_parser().parse_args([command, *flags])
                self.assertEqual(raised.exception.code, 2)

    def test_policy_resolved_before_loading_and_passed_to_each_scan_path(self):
        empty = ScanResult.from_iterables([])
        for command, flags, expected in POLICIES:
            for detailed in (False, True):
                with (
                    self.subTest(command=command, flags=flags, detailed=detailed),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    arguments = [command, *flags]
                    if detailed:
                        arguments += [
                            "--dedupe-report",
                            str(Path(directory) / "dedupe.json"),
                        ]
                    args = cli.build_parser().parse_args(arguments)

                    def load(
                        *unused_args, namespace=args, policy=expected, **unused_kwargs
                    ):
                        self.assertIs(namespace.allow_partial, policy)
                        return []

                    with (
                        mock.patch("swe_scraper.cli.load_targets", side_effect=load),
                        mock.patch(
                            "swe_scraper.cli.scan_targets", return_value=empty
                        ) as scan,
                        mock.patch(
                            "swe_scraper.cli.scan_targets_detailed",
                            return_value=ScanDetails(empty, ()),
                        ) as scan_detailed,
                    ):
                        self.assertEqual(cli._run_scan(args), empty)
                    selected = scan_detailed if detailed else scan
                    selected.assert_called_once()
                    self.assertIs(selected.call_args.kwargs["allow_partial"], expected)
                    (scan if detailed else scan_detailed).assert_not_called()

    def test_resolved_policy_reaches_real_provider_and_fake_client(self):
        target = Target("greenhouse", "Acme", "acme")
        row = {
            "id": "1",
            "title": "Software Engineering Intern",
            "absolute_url": sample_job().application_url,
        }
        for command, flags, expected in POLICIES:
            for detailed in (False, True):
                with (
                    self.subTest(command=command, flags=flags, detailed=detailed),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    arguments = [command, *flags]
                    if detailed:
                        arguments += [
                            "--dedupe-report",
                            str(Path(directory) / "dedupe.json"),
                        ]
                    args = cli.build_parser().parse_args(arguments)
                    client = mock.Mock()
                    client.get_json.return_value = {"jobs": [row, None]}
                    with (
                        mock.patch("swe_scraper.cli.load_targets", return_value=[target]),
                        mock.patch(
                            "swe_scraper.scanner.RequestsJsonClient", return_value=client
                        ),
                    ):
                        result = cli._run_scan(args)
                    self.assertEqual(len(result.jobs), int(expected))
                    self.assertEqual(len(result.errors), 1)
                    if expected:
                        self.assertIs(result.jobs[0].metadata["board_complete"], False)
                    client.get_json.assert_called_once_with(
                        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
                    )

    def test_checkpoint_fingerprint_uses_resolved_policy_for_both_scan_paths(self):
        empty = ScanResult.from_iterables([])
        for detailed in (False, True):
            fingerprints = []
            for flags, expected in (
                ([], True),
                (["--allow-partial"], True),
                (["--complete-boards-only"], False),
            ):
                with (
                    self.subTest(flags=flags, detailed=detailed),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    arguments = [
                        "scan",
                        "--target-set",
                        "all",
                        "--no-progress",
                        *flags,
                        "--output",
                        str(root / "jobs.json"),
                    ]
                    if detailed:
                        arguments += ["--dedupe-report", str(root / "dedupe.json")]
                    args = cli.build_parser().parse_args(arguments)
                    with (
                        mock.patch("swe_scraper.cli.load_targets", return_value=[]),
                        mock.patch("swe_scraper.cli.scan_targets", return_value=empty),
                        mock.patch(
                            "swe_scraper.cli.scan_targets_detailed",
                            return_value=ScanDetails(empty, ()),
                        ),
                        mock.patch(
                            "swe_scraper.checkpoints._default_cache_root",
                            return_value=root / "cache",
                        ),
                        mock.patch(
                            "swe_scraper.cli.ScanCheckpointStore", wraps=ScanCheckpointStore
                        ) as create,
                    ):
                        cli._run_scan(args)
                    self.assertIs(
                        create.call_args.kwargs["scan_options"]["allow_partial"], expected
                    )
                    manifest = json.loads(
                        (args._checkpoint_store.path / "manifest.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    fingerprints.append(manifest["fingerprint"])
            self.assertEqual(fingerprints[0], fingerprints[1])
            self.assertNotEqual(fingerprints[0], fingerprints[2])


class CliStatusTests(unittest.TestCase):
    def test_scan_and_watch_once_json_csv_status_matrix(self):
        cases = (
            ([sample_job()], [], 0),
            ([], [], 0),
            ([sample_job()], [failure()], 1),
            ([], [failure()], 2),
        )
        for command in ("scan", "watch"):
            for output_format in ("json", "csv"):
                for flags in (
                    [],
                    ["--allow-partial"],
                    ["--complete-boards-only"],
                    ["--strict"],
                ):
                    for jobs, errors, expected in cases:
                        with (
                            self.subTest(
                                command=command,
                                format=output_format,
                                flags=flags,
                                jobs=len(jobs),
                                errors=len(errors),
                            ),
                            tempfile.TemporaryDirectory() as directory,
                            contextlib.redirect_stdout(io.StringIO()),
                            contextlib.redirect_stderr(io.StringIO()) as stderr,
                        ):
                            output = Path(directory) / f"jobs.{output_format}"
                            arguments = [
                                command,
                                *flags,
                                "--output",
                                str(output),
                                "--format",
                                output_format,
                            ]
                            if command == "watch":
                                arguments += [
                                    "--once",
                                    "--state",
                                    str(Path(directory) / "seen.json"),
                                ]
                            result = ScanResult.from_iterables(jobs, errors)
                            with mock.patch(
                                "swe_scraper.cli._run_scan", return_value=result
                            ):
                                self.assertEqual(cli.main(arguments), expected)
                            if output_format == "json":
                                saved = json.loads(output.read_text(encoding="utf-8"))
                                self.assertEqual(saved["job_count"], len(jobs))
                                self.assertEqual(saved["error_count"], len(errors))
                            else:
                                with output.open(
                                    encoding="utf-8-sig", newline=""
                                ) as handle:
                                    self.assertEqual(
                                        len(list(csv.DictReader(handle))), len(jobs)
                                    )
                            if errors:
                                self.assertIn("greenhouse/broken", stderr.getvalue())
                                self.assertIn("board unavailable", stderr.getvalue())
                            else:
                                self.assertEqual(stderr.getvalue(), "")

    def test_nine_healthy_empty_boards_and_one_failure_return_two(self):
        targets = [
            Target("greenhouse", f"Board {index}", str(index)) for index in range(10)
        ]
        for command in ("scan", "watch"):
            with (
                self.subTest(command=command),
                tempfile.TemporaryDirectory() as directory,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                client = mock.Mock()
                client.get_json.side_effect = [{"jobs": []}] * 9 + [
                    RuntimeError("unavailable")
                ]
                arguments = [
                    command,
                    "--max-workers",
                    "1",
                    "--output",
                    str(Path(directory) / "jobs.json"),
                ]
                if command == "watch":
                    arguments += ["--once", "--state", str(Path(directory) / "seen.json")]
                with (
                    mock.patch("swe_scraper.cli.load_targets", return_value=targets),
                    mock.patch(
                        "swe_scraper.scanner.RequestsJsonClient", return_value=client
                    ),
                ):
                    self.assertEqual(cli.main(arguments), 2)
                self.assertEqual(client.get_json.call_count, 10)

    def test_strict_scan_finishes_targets_and_exports_jobs_after_failure(self):
        targets = [
            Target("greenhouse", "Broken", "broken"),
            Target("greenhouse", "Acme", "acme"),
        ]
        client = mock.Mock()
        client.get_json.side_effect = [
            RuntimeError("unavailable"),
            {
                "jobs": [
                    {
                        "id": "1",
                        "title": "Software Engineering Intern",
                        "absolute_url": sample_job().application_url,
                    }
                ]
            },
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            mock.patch("swe_scraper.cli.load_targets", return_value=targets),
            mock.patch("swe_scraper.scanner.RequestsJsonClient", return_value=client),
        ):
            output = Path(directory) / "jobs.json"
            self.assertEqual(
                cli.main(
                    ["scan", "--strict", "--max-workers", "1", "--output", str(output)]
                ),
                1,
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["job_count"], 1)
        self.assertEqual(client.get_json.call_count, 2)

    def test_failure_diagnostics_are_sorted_limited_and_leave_json_errors_intact(self):
        errors = [
            ProviderFailure(
                "greenhouse",
                f"Board {index:02}",
                f"board{index:02}",
                "first line\nsecond line " + "x" * 2000,
            )
            for index in range(12)
        ]
        for command in ("scan", "watch"):
            messages = []
            for failures in (errors, list(reversed(errors))):
                with (
                    self.subTest(command=command, reversed=failures != errors),
                    tempfile.TemporaryDirectory() as directory,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    output = Path(directory) / "jobs.json"
                    arguments = [command, "--output", str(output)]
                    if command == "watch":
                        arguments += [
                            "--once",
                            "--state",
                            str(Path(directory) / "seen.json"),
                        ]
                    result = ScanResult.from_iterables([sample_job()], failures)
                    with mock.patch("swe_scraper.cli._run_scan", return_value=result):
                        self.assertEqual(cli.main(arguments), 1)
                    lines = stderr.getvalue().splitlines()
                    details = [line for line in lines if "greenhouse/board" in line]
                    self.assertEqual(len(details), 10)
                    for index, line in enumerate(details):
                        self.assertIn(f"board{index:02}", line)
                    self.assertLessEqual(len(lines), 12)
                    self.assertTrue(all(len(line) <= 400 for line in lines))
                    self.assertIn("2 additional failed board", stderr.getvalue())
                    saved = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual(
                        saved["errors"], [error.to_dict() for error in failures]
                    )
                    messages.append(stderr.getvalue())
            self.assertEqual(messages[0], messages[1])

    def test_diagnostic_identifiers_are_bounded_too(self):
        error = ProviderFailure("p" * 2000, "c" * 2000, "s" * 2000, "e" * 2000)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            cli._print_scan_errors(ScanResult.from_iterables([], [error]))
        self.assertTrue(all(len(line) <= 400 for line in stderr.getvalue().splitlines()))

    def test_interrupt_returns_130_for_scan_and_watch_once(self):
        for command in ("scan", "watch"):
            with (
                self.subTest(command=command),
                mock.patch("swe_scraper.cli._run_scan", side_effect=KeyboardInterrupt),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                arguments = [command] + (["--once"] if command == "watch" else [])
                self.assertEqual(cli.main(arguments), 130)


class CliRecurringWatchTests(unittest.TestCase):
    def test_watch_continues_after_provider_failures_for_every_recovery_policy(self):
        for flags, expected in (
            ([], False),
            (["--allow-partial"], True),
            (["--complete-boards-only"], False),
        ):
            for jobs in ([], [sample_job()]):
                with (
                    self.subTest(flags=flags, jobs=bool(jobs)),
                    tempfile.TemporaryDirectory() as directory,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                    mock.patch("swe_scraper.cli.load_targets", return_value=[]),
                    mock.patch(
                        "swe_scraper.cli.scan_targets",
                        side_effect=[
                            ScanResult.from_iterables(jobs, [failure()]),
                            ScanResult.from_iterables([sample_job()]),
                            KeyboardInterrupt,
                        ],
                    ) as scan,
                    mock.patch("swe_scraper.cli.time.sleep") as sleep,
                ):
                    root = Path(directory)
                    self.assertEqual(
                        cli.main(
                            [
                                "watch",
                                *flags,
                                "--interval",
                                "60",
                                "--output",
                                str(root / "jobs.json"),
                                "--state",
                                str(root / "seen.json"),
                            ]
                        ),
                        130,
                    )
                    self.assertEqual(scan.call_count, 3)
                    self.assertEqual(sleep.call_args_list, [mock.call(60), mock.call(60)])
                    for call in scan.call_args_list:
                        self.assertIs(call.kwargs["allow_partial"], expected)
                    self.assertEqual(
                        json.loads((root / "jobs.json").read_text())["error_count"], 0
                    )

    def test_strict_watch_stops_after_export_for_every_recovery_policy(self):
        for flags in ([], ["--allow-partial"], ["--complete-boards-only"]):
            for jobs, expected in (([], 2), ([sample_job()], 1)):
                with (
                    self.subTest(flags=flags, jobs=bool(jobs)),
                    tempfile.TemporaryDirectory() as directory,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                    mock.patch(
                        "swe_scraper.cli._run_scan",
                        return_value=ScanResult.from_iterables(jobs, [failure()]),
                    ) as scan,
                    mock.patch("swe_scraper.cli.time.sleep") as sleep,
                ):
                    root = Path(directory)
                    self.assertEqual(
                        cli.main(
                            [
                                "watch",
                                "--strict",
                                *flags,
                                "--output",
                                str(root / "jobs.json"),
                                "--state",
                                str(root / "seen.json"),
                            ]
                        ),
                        expected,
                    )
                    scan.assert_called_once()
                    sleep.assert_not_called()
                    self.assertEqual(
                        json.loads((root / "jobs.json").read_text())["job_count"], len(jobs)
                    )

    def test_watch_configuration_export_and_notifier_errors_do_not_repeat(self):
        for operation, exception in (
            ("_run_scan", ValueError("invalid configuration")),
            ("_write_result", OSError("export failed")),
            ("JsonLinesNotifier.notify", RuntimeError("notifier failed")),
        ):
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory() as directory,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
                mock.patch(
                    "swe_scraper.cli._run_scan",
                    return_value=ScanResult.from_iterables([sample_job()]),
                ),
                mock.patch(f"swe_scraper.cli.{operation}", side_effect=exception),
                mock.patch("swe_scraper.cli.time.sleep") as sleep,
                self.assertRaises(SystemExit) as raised,
            ):
                root = Path(directory)
                cli.main(
                    [
                        "watch",
                        "--allow-partial",
                        "--output",
                        str(root / "jobs.json"),
                        "--state",
                        str(root / "seen.json"),
                        "--notify-jsonl",
                        str(root / "events.jsonl"),
                    ]
                )
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(str(exception), stderr.getvalue())
            sleep.assert_not_called()


class CliCheckpointExportTests(unittest.TestCase):
    def test_checkpoints_clear_only_after_successful_complete_export(self):
        for errors in ([], [failure()]):
            with (
                self.subTest(errors=bool(errors)),
                tempfile.TemporaryDirectory() as directory,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch("swe_scraper.cli.load_targets", return_value=[]),
                mock.patch(
                    "swe_scraper.cli.scan_targets",
                    return_value=ScanResult.from_iterables([], errors),
                ),
            ):
                root = Path(directory)
                args = cli.build_parser().parse_args(
                    [
                        "scan",
                        "--target-set",
                        "all",
                        "--no-progress",
                        "--output",
                        str(root / "jobs.json"),
                    ]
                )
                with mock.patch(
                    "swe_scraper.checkpoints._default_cache_root",
                    return_value=root / "cache",
                ):
                    self.assertEqual(args.handler(args), 2 if errors else 0)
                self.assertEqual(args._checkpoint_store.path.exists(), bool(errors))
                self.assertTrue((root / "jobs.json").exists())

    def test_atomic_export_failure_preserves_previous_output_and_completed_checkpoints(
        self,
    ):
        target = Target("greenhouse", "Acme", "acme")
        replace = cli.os.replace
        for output_format in ("json", "csv"):
            with (
                self.subTest(format=output_format),
                tempfile.TemporaryDirectory() as directory,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
                mock.patch("swe_scraper.cli.load_targets", return_value=[target]),
            ):
                root = Path(directory)
                output = root / f"jobs.{output_format}"
                output.write_bytes(b"previous complete output\n")
                stores = []

                def scan(targets, saved_stores=stores, **options):
                    store = options["checkpoint_store"]
                    store.save(0, target, [sample_job()])
                    saved_stores.append(store)
                    return ScanResult.from_iterables([sample_job()])

                def fail_export(source, destination, export_path=output):
                    if Path(destination) == export_path:
                        raise OSError("atomic export failed")
                    return replace(source, destination)

                with (
                    mock.patch("swe_scraper.cli.scan_targets", side_effect=scan),
                    mock.patch(
                        "swe_scraper.checkpoints._default_cache_root",
                        return_value=root / "cache",
                    ),
                    mock.patch("swe_scraper.cli.os.replace", side_effect=fail_export),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli.main(
                        [
                            "scan",
                            "--target-set",
                            "all",
                            "--no-progress",
                            "--output",
                            str(output),
                            "--format",
                            output_format,
                        ]
                    )
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("atomic export failed", stderr.getvalue())
                self.assertEqual(output.read_bytes(), b"previous complete output\n")
                self.assertEqual(stores[0].load(0, target), [sample_job()])
                self.assertTrue((stores[0].path / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
