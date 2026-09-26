"""Audit a staged public repository and write a machine-readable report."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from inspect_distribution import inspect as inspect_distribution

TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".txt", ".yml", ".yaml"}
GENERATED_PARTS = {"__pycache__", "build", "dist"}
TEXT_SCAN_EXCLUSIONS = {
    "scripts/audit_public_repo.py",
    "scripts/inspect_distribution.py",
    "scripts/stage_public_repo.py",
}
FORBIDDEN_ROOTS = {"config", "data", "tools"}
FORBIDDEN_NAMES = {
    "applicant-profile.example.json",
    "applicant.json",
    "applications.db",
    "compat.py",
    "personal-profile.example.json",
    "personal.json",
}


def source_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        relative_parts = path.relative_to(root).parts
        generated = any(
            part in GENERATED_PARTS or part.endswith(".egg-info") for part in relative_parts
        )
        if path.is_file() and not generated:
            paths.append(path)
    return paths


def find_boundary_violations(root: Path, paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(root)
        if relative.parts[0].casefold() in FORBIDDEN_ROOTS:
            violations.append(relative.as_posix())
        if path.name.casefold() in FORBIDDEN_NAMES:
            violations.append(relative.as_posix())
    return sorted(set(violations))


def scan_text(root: Path, paths: list[Path]) -> tuple[list[str], list[str]]:
    secret_fragments = (
        "BEGIN " + "PRIVATE KEY",
        "BEGIN " + "OPENSSH PRIVATE KEY",
    )
    credential = re.compile(
        r"(?i)(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*"
        r"['\"][A-Za-z0-9_./+=-]{12,}['\"]"
    )
    windows_user = re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+")
    email = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
    secrets: list[str] = []
    pii: list[str] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.suffix.casefold() not in TEXT_SUFFIXES or relative in TEXT_SCAN_EXCLUSIONS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        has_secret_fragment = any(fragment in text for fragment in secret_fragments)
        if has_secret_fragment or credential.search(text):
            secrets.append(relative)
        if windows_user.search(text):
            pii.append(relative)
        for address in email.findall(text):
            if not address.casefold().endswith(("@example.com", "@example.test")):
                pii.append(relative)
                break
    return sorted(set(secrets)), sorted(set(pii))


def validate_url(value: str, label: str, failures: list[str]) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        failures.append(label)


def scan_urls(root: Path) -> tuple[int, list[str]]:
    catalog = json.loads(
        (root / "src" / "swe_scraper" / "data" / "targets.json").read_text(encoding="utf-8")
    )
    checked = 0
    failures: list[str] = []
    for provider, targets in catalog.items():
        for target in targets:
            for key in ("origin", "search_url"):
                value = target.get(key)
                if value:
                    checked += 1
                    validate_url(value, f"{provider}:{target['name']}:{key}", failures)

    for fixture in (root / "tests" / "fixtures").rglob("*.json"):
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key, value in item.items():
                    if isinstance(value, str) and key.casefold().endswith("url"):
                        checked += 1
                        validate_url(value, fixture.relative_to(root).as_posix(), failures)
                    elif isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(item, list):
                stack.extend(item)
    return checked, sorted(set(failures))


def run(root: Path, artifact_directory: Path) -> dict[str, object]:
    paths = source_files(root)
    boundary = find_boundary_violations(root, paths)
    secrets, pii = scan_text(root, paths)
    checked_urls, invalid_urls = scan_urls(root)
    artifacts = sorted(artifact_directory.glob("*.whl")) + sorted(
        artifact_directory.glob("*.tar.gz")
    )
    artifact_errors: list[str] = []
    for artifact in artifacts:
        try:
            inspect_distribution(artifact)
        except Exception as exc:
            artifact_errors.append(f"{artifact.name}: {exc}")

    license_path = root / "LICENSE"
    plugin_license = root / "plugins" / "icims" / "LICENSE"
    license_ok = (
        license_path.is_file()
        and plugin_license.is_file()
        and license_path.read_bytes() == plugin_license.read_bytes()
        and "MIT License" in license_path.read_text(encoding="utf-8")
    )
    checks = {
        "clean_history": not (root / ".git").exists(),
        "boundary": not boundary,
        "secrets": not secrets,
        "pii": not pii,
        "licenses": license_ok,
        "catalog_urls": not invalid_urls and checked_urls > 0,
        "artifacts": bool(artifacts) and not artifact_errors,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "evidence": {
            "source_files": len(paths),
            "artifacts": [artifact.name for artifact in artifacts],
            "catalog_and_fixture_urls_checked": checked_urls,
            "boundary_violations": boundary,
            "secret_findings": secrets,
            "pii_findings": pii,
            "invalid_urls": invalid_urls,
            "artifact_errors": artifact_errors,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path, default=Path("dist"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/public-release-audit.json")
    )
    args = parser.parse_args()
    report = run(args.root.resolve(), args.artifacts.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {args.output}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
