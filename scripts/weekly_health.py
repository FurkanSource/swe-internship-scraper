"""Run a deterministic weekly rotation across every core provider."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_scraper.health import run_health_checks  # noqa: E402
from swe_scraper.providers.base import Target  # noqa: E402

CORE_PROVIDERS = (
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "smartrecruiters",
    "oracle",
)


def select_targets(
    catalog: dict[str, object], *, per_provider: int, seed: str
) -> list[Target]:
    selected: list[Target] = []
    for provider in CORE_PROVIDERS:
        raw_rows = catalog.get(provider)
        if not isinstance(raw_rows, list) or len(raw_rows) < per_provider:
            raise ValueError(
                f"provider '{provider}' has fewer than {per_provider} catalog targets"
            )
        rows = [row for row in raw_rows if isinstance(row, dict)]

        def selection_key(
            row: dict[str, object], provider_name: str = provider
        ) -> tuple[bytes, str, str]:
            identity = (
                f"{seed}:{provider_name}:{row.get('slug', '')}:{row.get('origin', '')}"
            )
            return (
                hashlib.sha256(identity.encode("utf-8")).digest(),
                str(row.get("name", "")),
                str(row.get("slug", "")),
            )

        rows.sort(key=selection_key)
        for row in rows[:per_provider]:
            selected.append(Target.from_mapping(provider, row))
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-provider", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--output", type=Path, default=Path("weekly-health.json"))
    args = parser.parse_args()
    if args.per_provider < 1:
        raise ValueError("per-provider must be positive")

    catalog_path = ROOT / "src" / "swe_scraper" / "data" / "targets.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    year, week, _ = dt.date.today().isocalendar()
    seed = f"{year}-W{week:02d}"
    targets = select_targets(catalog, per_provider=args.per_provider, seed=seed)
    report = run_health_checks(
        targets,
        max_workers=args.max_workers,
        quorum=max(2, min(args.per_provider, 8)),
    )
    payload = report.to_dict()
    payload["rotation_seed"] = seed
    payload["targets_per_provider"] = args.per_provider
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Weekly health is {report.status.value}: {len(targets)} targets")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
