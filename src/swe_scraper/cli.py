"""Command-line interface for public scans, validation, watching, and tracking."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import TextIO

from . import __version__
from .checkpoints import ScanCheckpointStore
from .config import load_targets
from .detail_cache import DetailCache
from .exporters import write_csv, write_json
from .health import run_health_checks
from .models import ScanResult
from .notifications import JsonLinesNotifier
from .providers.http import RequestsJsonClient
from .providers.registry import DEFAULT_REGISTRY
from .scanner import ScanProgress, scan_targets, scan_targets_detailed
from .validation import validate_file
from .watch import load_seen, save_seen, unseen_jobs


def _console_print(message: str, *, file: TextIO | None = None) -> None:
    """Preserve Unicode in exports while escaping unsupported console characters."""
    stream = file if file is not None else sys.stdout
    encoding = getattr(stream, "encoding", None)
    encoding = encoding if isinstance(encoding, str) else "utf-8"
    safe = message.encode(encoding, errors="backslashreplace").decode(encoding)
    print(safe, file=stream)


def _csv_values(values: list[str] | None) -> list[str]:
    output: list[str] = []
    for value in values or []:
        output.extend(part.strip() for part in value.split(",") if part.strip())
    return output


def _add_scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--targets", type=Path, help="JSON target configuration")
    parser.add_argument(
        "--providers",
        action="append",
        metavar="NAME[,NAME]",
        help=(
            "providers to scan (greenhouse, lever, ashby, workday, smartrecruiters, oracle)"
        ),
    )
    parser.add_argument(
        "--location", action="append", default=[], help="location substring"
    )
    parser.add_argument("--include", action="append", default=[], help="required keyword")
    parser.add_argument("--exclude", action="append", default=[], help="excluded keyword")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument(
        "--cache-ttl",
        type=int,
        default=0,
        metavar="SECONDS",
        help="reuse unchanged posting details for 1-3600 seconds (default: disabled)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="refresh detail cache entries instead of reusing them",
    )
    target_set = parser.add_mutually_exclusive_group()
    target_set.add_argument(
        "--quick",
        action="store_true",
        help="scan the small sample of monitored boards",
    )
    target_set.add_argument(
        "--target-set",
        choices=("priority", "all", "canary"),
        default="priority",
        help="bundled target profile (default: priority)",
    )
    role_filter = parser.add_mutually_exclusive_group()
    role_filter.add_argument(
        "--all-jobs",
        action="store_true",
        help="return every ATS posting instead of SWE internships only",
    )
    role_filter.add_argument(
        "--include-adjacent",
        action="store_true",
        help="also include adjacent technical internships (analytics, research, IT)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero on any provider error, after saving available results",
    )
    parser.add_argument(
        "--dedupe-report",
        type=Path,
        help="write uncertain duplicate candidates without merging them",
    )


def _run_scan(args: argparse.Namespace) -> ScanResult:
    cache_ttl = getattr(args, "cache_ttl", 0)
    if not 0 <= cache_ttl <= 3600:
        raise ValueError("--cache-ttl must be between 0 and 3600 seconds")
    if getattr(args, "refresh", False) and getattr(args, "resume", False):
        raise ValueError("--refresh requires a fresh scan without --resume")
    if args.quick and args.targets is not None:
        raise ValueError(
            "--quick uses bundled canaries; for custom --targets use "
            "--target-set canary and explicit canary profiles"
        )
    providers = _csv_values(args.providers)
    profile = "canary" if args.quick else args.target_set
    targets = load_targets(args.targets, providers, profile=profile)
    options = {
        "max_workers": args.max_workers,
        "filter_swe": not args.all_jobs,
        "include_adjacent": args.include_adjacent,
        "locations": args.location,
        "include_keywords": args.include,
        "exclude_keywords": args.exclude,
    }
    if cache_ttl:
        http = RequestsJsonClient(
            detail_cache=DetailCache(ttl_seconds=cache_ttl),
            refresh_cache=getattr(args, "refresh", False),
        )
        options["client"] = http
        args._detail_client = http
    if getattr(args, "resume", False) is True and profile != "all":
        raise ValueError("--resume requires --target-set all")
    if args.command == "scan" and profile == "all":
        scan_options = {
            key: value
            for key, value in options.items()
            if key not in {"max_workers", "client"}
        }
        scan_options["detail_cache_ttl"] = cache_ttl
        checkpoint_store = ScanCheckpointStore(
            output=args.output,
            targets=targets,
            scan_options=scan_options,
            version=__version__,
            resume=args.resume,
        )
        args._checkpoint_store = checkpoint_store
        options["checkpoint_store"] = checkpoint_store
        if not args.no_progress:
            last_printed = float("-inf")

            def report(progress: ScanProgress) -> None:
                nonlocal last_printed
                now = time.monotonic()
                if now - last_printed < 10 and progress.completed != progress.total:
                    return
                last_printed = now
                _console_print(
                    f"Scan {progress.completed}/{progress.total} boards; "
                    f"{progress.resumed} resumed; "
                    f"{progress.fetched_records} records fetched; "
                    f"{progress.failed} failed; {progress.elapsed_seconds:.0f}s elapsed",
                    file=sys.stderr,
                )

            options["progress_callback"] = report
    report_path = getattr(args, "dedupe_report", None)
    if report_path is None:
        return scan_targets(targets, **options)
    details = scan_targets_detailed(targets, **options)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "candidate_count": len(details.potential_duplicates),
        "potential_duplicates": [
            {
                "left_id": value.left_id,
                "right_id": value.right_id,
                "reason": value.reason,
                "confidence": value.confidence,
            }
            for value in details.potential_duplicates
        ],
    }
    temporary = report_path.with_name(f".{report_path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, report_path)
    return details.result


def _write_result(result: ScanResult, output: Path, output_format: str) -> Path:
    selected = output_format
    if selected == "auto":
        selected = "csv" if output.suffix.casefold() == ".csv" else "json"
    return write_csv(result, output) if selected == "csv" else write_json(result, output)


def _scan_exit_code(result: ScanResult, strict: bool) -> int:
    if result.errors and not result.jobs:
        return 2
    return 1 if strict and result.errors else 0


def _scan_command(args: argparse.Namespace) -> int:
    result = _run_scan(args)
    target = _write_result(result, args.output, args.format)
    checkpoint_store = getattr(args, "_checkpoint_store", None)
    if checkpoint_store is not None and not result.errors:
        checkpoint_store.clear()
    _console_print(f"Wrote {len(result.jobs)} jobs to {target}")
    _print_cache_stats(args)
    if result.errors:
        _console_print(
            f"Completed with {len(result.errors)} provider error(s)", file=sys.stderr
        )
    return _scan_exit_code(result, args.strict)


def _print_cache_stats(args: argparse.Namespace) -> None:
    client = getattr(args, "_detail_client", None)
    if client is not None:
        _console_print(
            f"Details: {client.detail_requests} fetched; "
            f"{client.detail_cache_hits} reused from cache",
            file=sys.stderr,
        )


def _validate_command(args: argparse.Namespace) -> int:
    report = validate_file(args.path)
    if report.valid:
        _console_print(f"Valid: {report.jobs} job record(s)")
        return 0
    _console_print(f"Invalid: {len(report.errors)} error(s)", file=sys.stderr)
    for error in report.errors[:20]:
        _console_print(f"  - {error}", file=sys.stderr)
    return 1


def _watch_command(args: argparse.Namespace) -> int:
    if not args.once and args.interval < 60:
        raise ValueError("watch interval must be at least 60 seconds")
    try:
        while True:
            result = _run_scan(args)
            target = _write_result(result, args.output, args.format)
            seen = load_seen(args.state)
            new_jobs = unseen_jobs(result.jobs, seen)
            _print_cache_stats(args)
            _console_print(
                f"Wrote {len(result.jobs)} jobs to {target}; "
                f"{len(new_jobs)} new; {len(result.errors)} provider error(s)"
            )
            for job in new_jobs[:20]:
                _console_print(f"  {job.company} — {job.title}: {job.application_url}")
            if new_jobs and args.notify_jsonl:
                JsonLinesNotifier(args.notify_jsonl).notify(new_jobs)
            if result.jobs or not result.errors:
                save_seen(args.state, result.jobs)
            if args.once or (args.strict and result.errors):
                return _scan_exit_code(result, args.strict)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130


def _health_command(args: argparse.Namespace) -> int:
    targets = load_targets(args.targets, _csv_values(args.providers), profile="canary")
    report = run_health_checks(
        targets,
        max_workers=args.max_workers,
        quorum=args.quorum,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    _console_print(
        f"Provider health is {report.status.value}: "
        f"{len(report.provider_statuses)} providers checked"
    )
    return report.exit_code


def _providers_command(args: argparse.Namespace) -> int:
    inventory = DEFAULT_REGISTRY.describe()
    if args.json:
        _console_print(json.dumps(inventory, indent=2, ensure_ascii=True))
        return 0
    for row in inventory:
        source = "built-in" if row["builtin"] else "plugin"
        required = ", ".join(row["required_options"]) or "none"
        _console_print(f"{row['name']} ({source}) - required target options: {required}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swe-scraper",
        description="Find software engineering internships on official ATS boards.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="scan official ATS boards")
    _add_scan_options(scan)
    scan.add_argument("--output", type=Path, default=Path("jobs.json"))
    scan.add_argument("--format", choices=("auto", "json", "csv"), default="auto")
    scan.add_argument(
        "--resume",
        action="store_true",
        help="resume a matching full-catalog scan from saved board checkpoints",
    )
    scan.add_argument(
        "--no-progress", action="store_true", help="hide full-catalog progress messages"
    )
    scan.set_defaults(handler=_scan_command)

    validate = commands.add_parser("validate", help="validate a JSON scan artifact")
    validate.add_argument("path", type=Path)
    validate.set_defaults(handler=_validate_command)

    watch = commands.add_parser("watch", help="repeat scans and report newly seen jobs")
    _add_scan_options(watch)
    watch.add_argument("--output", type=Path, default=Path("jobs-latest.json"))
    watch.add_argument("--format", choices=("auto", "json", "csv"), default="auto")
    watch.add_argument("--state", type=Path, default=Path(".swe-scraper-seen.json"))
    watch.add_argument("--interval", type=int, default=21_600, help="seconds between scans")
    watch.add_argument("--once", action="store_true", help="perform one watch iteration")
    watch.add_argument(
        "--notify-jsonl",
        type=Path,
        help="append new-job events for a downstream notification service",
    )
    watch.set_defaults(handler=_watch_command)

    health = commands.add_parser(
        "health", help="check configured canaries with provider quorum rules"
    )
    health.add_argument("--targets", type=Path, help="JSON target configuration")
    health.add_argument("--providers", action="append", metavar="NAME[,NAME]")
    health.add_argument("--max-workers", type=int, default=4)
    health.add_argument("--quorum", type=int, default=2)
    health.add_argument("--output", type=Path, default=Path("provider-health.json"))
    health.set_defaults(handler=_health_command)

    providers = commands.add_parser(
        "providers", help="list built-in and installed provider plugins"
    )
    providers.add_argument("--json", action="store_true", help="emit JSON")
    providers.set_defaults(handler=_providers_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, RuntimeError, ValueError) as exc:
        _console_print(f"error: {exc}", file=sys.stderr)
        parser.exit(2)
