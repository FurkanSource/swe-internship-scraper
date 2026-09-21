"""Command-line interface for public scans, validation, watching, and tracking."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from .config import load_targets
from .exporters import write_csv, write_json
from .health import run_health_checks
from .models import ScanResult
from .notifications import JsonLinesNotifier
from .providers.registry import DEFAULT_REGISTRY
from .scanner import scan_targets, scan_targets_detailed
from .validation import validate_file
from .watch import load_seen, save_seen, unseen_jobs


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
        "--target-set",
        choices=("priority", "all", "canary"),
        default="priority",
        help="bundled target profile (default: priority)",
    )
    parser.add_argument(
        "--all-jobs",
        action="store_true",
        help="return every ATS posting instead of SWE internships only",
    )
    parser.add_argument(
        "--dedupe-report",
        type=Path,
        help="write uncertain duplicate candidates without merging them",
    )


def _run_scan(args: argparse.Namespace) -> ScanResult:
    providers = _csv_values(args.providers)
    targets = load_targets(args.targets, providers, profile=args.target_set)
    options = {
        "max_workers": args.max_workers,
        "filter_swe": not args.all_jobs,
        "locations": args.location,
        "include_keywords": args.include,
        "exclude_keywords": args.exclude,
    }
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


def _scan_command(args: argparse.Namespace) -> int:
    result = _run_scan(args)
    target = _write_result(result, args.output, args.format)
    print(f"Wrote {len(result.jobs)} jobs to {target}")
    if result.errors:
        print(f"Completed with {len(result.errors)} provider error(s)", file=sys.stderr)
    return 2 if result.errors and not result.jobs else 0


def _validate_command(args: argparse.Namespace) -> int:
    report = validate_file(args.path)
    if report.valid:
        print(f"Valid: {report.jobs} job record(s)")
        return 0
    print(f"Invalid: {len(report.errors)} error(s)", file=sys.stderr)
    for error in report.errors[:20]:
        print(f"  - {error}", file=sys.stderr)
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
            print(
                f"Wrote {len(result.jobs)} jobs to {target}; "
                f"{len(new_jobs)} new; {len(result.errors)} provider error(s)"
            )
            for job in new_jobs[:20]:
                print(f"  {job.company} — {job.title}: {job.application_url}")
            if new_jobs and args.notify_jsonl:
                JsonLinesNotifier(args.notify_jsonl).notify(new_jobs)
            if result.jobs or not result.errors:
                save_seen(args.state, result.jobs)
            if args.once:
                return 2 if result.errors and not result.jobs else 0
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
    print(
        f"Provider health is {report.status.value}: "
        f"{len(report.provider_statuses)} providers checked"
    )
    return report.exit_code


def _providers_command(args: argparse.Namespace) -> int:
    inventory = DEFAULT_REGISTRY.describe()
    if args.json:
        print(json.dumps(inventory, indent=2, ensure_ascii=False))
        return 0
    for row in inventory:
        source = "built-in" if row["builtin"] else "plugin"
        required = ", ".join(row["required_options"]) or "none"
        print(f"{row['name']} ({source}) - required target options: {required}")
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
        parser.exit(2, f"error: {exc}\n")
