"""Discover and verify public SmartRecruiters and Oracle catalog targets."""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_scraper.providers.base import Target  # noqa: E402
from swe_scraper.providers.http import RequestsJsonClient  # noqa: E402
from swe_scraper.providers.oracle import OracleProvider  # noqa: E402
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider  # noqa: E402

ORACLE_PATH = re.compile(
    r"^/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/[^/]+",
    re.IGNORECASE,
)


def discover_candidates(listings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Extract stable provider targets from cached public posting URLs."""
    smartrecruiters: dict[str, str] = {}
    oracle: dict[tuple[str, str], str] = {}
    for item in listings:
        raw_url = str(item.get("url") or "").strip()
        company = str(item.get("company_name") or item.get("company") or "").strip()
        try:
            parsed = urlsplit(unquote(raw_url))
        except ValueError:
            continue
        hostname = (parsed.hostname or "").casefold()
        parts = [part for part in parsed.path.split("/") if part]
        if hostname == "jobs.smartrecruiters.com" and len(parts) >= 2:
            slug = parts[0]
            smartrecruiters.setdefault(slug.casefold(), company or slug)
            continue
        if hostname.endswith(".oraclecloud.com"):
            match = ORACLE_PATH.match(parsed.path)
            if match:
                origin = f"https://{parsed.netloc.casefold()}"
                site = match.group(1)
                oracle.setdefault((origin, site.casefold()), company or hostname)

    return {
        "smartrecruiters": [
            {"name": name, "slug": slug, "profiles": ["all"]}
            for slug, name in sorted(smartrecruiters.items())
        ],
        "oracle": [
            {
                "name": name,
                "slug": site,
                "origin": origin,
                "profiles": ["all"],
            }
            for (origin, site), name in sorted(oracle.items())
        ],
    }


def load_feed(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for entry in payload.values():
            if isinstance(entry, dict) and isinstance(entry.get("data"), list):
                rows.extend(row for row in entry["data"] if isinstance(row, dict))
    return rows


def _verify_smartrecruiters(
    row: dict[str, Any], client: RequestsJsonClient
) -> dict[str, Any]:
    target = Target.from_mapping("smartrecruiters", row)
    provider = SmartRecruitersProvider()
    provider.validate_target(target)
    endpoint = f"https://api.smartrecruiters.com/v1/companies/{target.slug}/postings"
    page = client.get_json(
        endpoint,
        params={"destination": "PUBLIC", "limit": 1, "offset": 0},
    )
    if not isinstance(page, dict) or not isinstance(page.get("content"), list):
        raise RuntimeError("invalid public posting list")
    if not isinstance(page.get("totalFound"), int):
        raise RuntimeError("public posting list omitted totalFound")
    if not page["content"]:
        raise RuntimeError("public board has no direct posting to verify")
    source_id = str(page["content"][0].get("id") or "").strip()
    detail = client.get_json(f"{endpoint}/{source_id}")
    job = provider.parse_detail(target, detail)
    if job is None or not job.application_url.startswith("https://"):
        raise RuntimeError("posting omitted a direct HTTPS application URL")
    return {**row, "_verified_job_count": int(page["totalFound"])}


def _verify_oracle(row: dict[str, Any], client: RequestsJsonClient) -> dict[str, Any]:
    target = Target.from_mapping("oracle", row)
    provider = OracleProvider()
    provider.validate_target(target)
    origin = str(target.options["origin"]).rstrip("/")
    headers = {
        "Accept-Language": "en-US",
        "Ora-Irc-Language": "en",
        "REST-Framework-Version": "4",
        "Referer": f"{origin}/hcmUI/CandidateExperience/en/sites/{target.slug}/jobs",
    }
    endpoint = provider._url(
        origin,
        "recruitingCEJobRequisitions",
        {
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={target.slug},limit=1,offset=0",
        },
    )
    page = client.get_json(endpoint, headers=headers)
    if not isinstance(page, dict):
        raise RuntimeError("invalid Oracle public requisition response")
    container = provider._list_container(page)
    if container.get("hasMore") not in {True, False}:
        raise RuntimeError("invalid Oracle public requisition pagination")
    rows = provider._list_rows(page)
    if not rows:
        raise RuntimeError("public board has no direct posting to verify")
    source_id = provider._source_id(rows[0])
    detail_url = provider._url(
        origin,
        "recruitingCEJobRequisitionDetails",
        {
            "expand": "all",
            "finder": f'ById;Id="{source_id}",siteNumber="{target.slug}"',
        },
    )
    detail_payload = client.get_json(detail_url, headers=headers)
    details = detail_payload.get("items") if isinstance(detail_payload, dict) else None
    if not isinstance(details, list) or not details:
        raise RuntimeError("Oracle detail endpoint returned no posting")
    job = provider.parse_detail(target, rows[0], details[0])
    if not job.application_url.startswith("https://"):
        raise RuntimeError("Oracle posting omitted a direct HTTPS application URL")
    total_jobs = 0
    outer_items = page.get("items") or []
    if outer_items and isinstance(outer_items[0], dict):
        total_jobs = int(outer_items[0].get("TotalJobsCount") or len(rows))
    return {**row, "_verified_job_count": total_jobs}


def verify_candidates(
    candidates: dict[str, list[dict[str, Any]]],
    *,
    workers: int,
    timeout: float,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    client = RequestsJsonClient(timeout=(timeout, timeout), min_host_interval=0.15)
    tasks = [
        (provider, row)
        for provider in ("smartrecruiters", "oracle")
        for row in candidates.get(provider, [])
    ]
    verified = {"smartrecruiters": [], "oracle": []}
    errors: list[dict[str, str]] = []

    def check(provider: str, row: dict[str, Any]) -> dict[str, Any]:
        if provider == "smartrecruiters":
            return _verify_smartrecruiters(row, client)
        return _verify_oracle(row, client)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        futures = {
            pool.submit(check, provider, row): (provider, row) for provider, row in tasks
        }
        for future in as_completed(futures):
            provider, row = futures[future]
            try:
                verified[provider].append(future.result())
            except Exception as exc:
                errors.append(
                    {
                        "provider": provider,
                        "name": str(row.get("name") or ""),
                        "slug": str(row.get("slug") or ""),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    for rows in verified.values():
        rows.sort(key=lambda row: (str(row["name"]).casefold(), str(row["slug"])))
    errors.sort(key=lambda row: (row["provider"], row["name"].casefold(), row["slug"]))
    return verified, errors


def merge_catalog(
    catalog: dict[str, Any], verified: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    for provider, rows in verified.items():
        rows = [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in rows
        ]
        current = catalog.setdefault(provider, [])
        if provider == "oracle":
            keys = {
                (str(row.get("origin", "")).casefold(), str(row["slug"]).casefold())
                for row in current
            }
            for row in rows:
                key = (str(row["origin"]).casefold(), str(row["slug"]).casefold())
                if key not in keys:
                    current.append(row)
                    keys.add(key)
        else:
            slugs = {str(row["slug"]).casefold() for row in current}
            for row in rows:
                if str(row["slug"]).casefold() not in slugs:
                    current.append(row)
                    slugs.add(str(row["slug"]).casefold())
        current.sort(key=lambda row: (str(row["name"]).casefold(), str(row["slug"])))
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "src" / "swe_scraper" / "data" / "targets.json",
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "reports" / "catalog-verification.json",
    )
    args = parser.parse_args()

    candidates = discover_candidates(load_feed(args.feed))
    print(
        f"Discovered {len(candidates['smartrecruiters'])} SmartRecruiters and "
        f"{len(candidates['oracle'])} Oracle targets"
    )
    selected = candidates
    errors: list[dict[str, str]] = []
    if args.verify:
        selected, errors = verify_candidates(
            candidates, workers=args.workers, timeout=args.timeout
        )
        print(
            f"Verified {len(selected['smartrecruiters'])} SmartRecruiters and "
            f"{len(selected['oracle'])} Oracle targets; {len(errors)} rejected"
        )
        report = args.report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps({"verified": selected, "errors": errors}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {report}")
    if args.export:
        if not args.verify:
            raise ValueError("--export requires --verify")
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        merge_catalog(catalog, selected)
        args.catalog.write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {args.catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
