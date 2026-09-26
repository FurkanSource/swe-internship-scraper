"""Fail stable core publishing closed unless the immutable candidate is proven."""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import subprocess
import zipfile
from collections import Counter
from pathlib import Path

UTC = dt.timezone.utc
PROVIDERS = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "oracle"}


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return parsed.astimezone(UTC)


def healthy(report):
    checks = report.get("checks", [])
    count = 3 if report.get("mode") == "daily" else 10
    return (
        report.get("status") == "healthy"
        and report.get("exit_code") == 0
        and Counter(check.get("provider") for check in checks)
        == Counter(dict.fromkeys(PROVIDERS, count))
        and len({(check.get("provider"), check.get("target")) for check in checks})
        == len(checks)
        and all(
            check.get("status") == "healthy" and check.get("pagination_complete") is True
            for check in checks
        )
    )


def evaluate_reports(reports, tag, sha, now, published):
    observations = []
    for report in reports:
        if (
            report.get("candidate_tag") != tag
            or report.get("candidate_sha") != sha
            or report.get("version") != tag.removeprefix("v")
            or report.get("mode") not in {"daily", "weekly"}
        ):
            continue
        checked = timestamp(report["checked_at"])
        if published <= checked <= now:
            observations.append((checked, report))
    daily = [
        (date.date(), report) for date, report in observations if report["mode"] == "daily"
    ]
    if not daily:
        return ["No daily observations for this published candidate"]
    latest = max(date for date, _ in daily)
    required = {latest - dt.timedelta(days=i) for i in range(7)}
    failures = []
    if latest < now.date() - dt.timedelta(days=1):
        failures.append("Daily evidence is stale")
    if not required <= {date for date, _ in daily}:
        failures.append("Seven consecutive distinct UTC dates are required")
    if any(not healthy(report) for date, report in daily if date in required):
        failures.append("A daily observation failed or was incomplete in the window")
    weekly = sorted(
        [
            (date, report)
            for date, report in observations
            if report["mode"] == "weekly" and date.date() >= min(required)
        ],
        key=lambda item: item[0],
    )
    if not weekly or not healthy(weekly[-1][1]):
        failures.append("A recent successful 60-target weekly observation is required")
    return failures


def check_metadata_only(before, after, candidate_version, stable_version):
    if not re.fullmatch(re.escape(stable_version) + r"rc[0-9]+", candidate_version):
        raise ValueError("Candidate must be an RC of the stable version")
    for path in set(before) | set(after):
        if before.get(path) == after.get(path) or path == "CHANGELOG.md":
            continue
        prefix = {
            "pyproject.toml": "version",
            "src/swe_scraper/__init__.py": "__version__",
        }.get(path)
        if not prefix or path not in before or path not in after:
            raise ValueError(f"Promotion changes candidate behavior: {path}")
        expected = before[path].replace(
            f'{prefix} = "{candidate_version}"', f'{prefix} = "{stable_version}"', 1
        )
        if expected == before[path] or after[path] != expected:
            raise ValueError(f"Promotion changes more than version metadata: {path}")


def command(*args):
    return subprocess.check_output(args)


def api(path):
    return json.loads(command("gh", "api", path))


def pages(path, key):
    output = json.loads(command("gh", "api", "--paginate", "--slurp", path))
    return [item for page in output for item in page[key]]


def check_remote(repo, config, stable_version):
    tag = config["tag"]
    sha = command("git", "rev-parse", f"refs/tags/{tag}^{{commit}}").decode().strip()
    release = api(f"repos/{repo}/releases/tags/{tag}")
    if release["draft"] or not release["prerelease"]:
        raise ValueError("Candidate must be a published prerelease")
    published = timestamp(release["published_at"])
    runs = pages(
        f"repos/{repo}/actions/workflows/release.yml/runs?head_sha={sha}&event=push&per_page=100",
        "workflow_runs",
    )
    if not any(
        run["head_branch"] == tag and run["conclusion"] == "success" for run in runs
    ):
        raise ValueError("Candidate release and post-publication installation must pass")
    paths = command("git", "diff", "--name-only", sha, "HEAD").decode().splitlines()
    before, after = {}, {}
    for path in paths:
        for ref, destination in ((sha, before), ("HEAD", after)):
            result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True)
            if result.returncode == 0:
                destination[path] = result.stdout.decode("utf-8")
    check_metadata_only(before, after, config["version"], stable_version)
    observations = []
    runs = pages(
        f"repos/{repo}/actions/workflows/candidate-stability.yml/runs?per_page=100",
        "workflow_runs",
    )
    now = dt.datetime.now(UTC)
    attempts = []
    for run in runs:
        if timestamp(run["created_at"]) < published:
            continue
        # A rerun must not erase a failed observation on the same day.
        for attempt in range(1, run["run_attempt"]):
            attempts.append(
                api(f"repos/{repo}/actions/runs/{run['id']}/attempts/{attempt}")
            )
        attempts.append(run)
    for run in attempts:
        if timestamp(run["created_at"]) < published:
            continue
        if run["status"] != "completed":
            raise ValueError(
                "Candidate observation is still running; retry after it finishes"
            )
        artifacts = pages(
            f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100", "artifacts"
        )
        selected = [
            item
            for item in artifacts
            if item["name"] == f"candidate-{run['id']}-{run['run_attempt']}"
        ]
        if len(selected) != 1 or selected[0]["expired"]:
            raise ValueError(f"Missing candidate evidence for run {run['id']}")
        raw = command(
            "gh", "api", f"repos/{repo}/actions/artifacts/{selected[0]['id']}/zip"
        )
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            report = json.loads(archive.read("stability.json"))
        if str(report.get("run_id")) != str(run["id"]) or str(
            report.get("run_attempt")
        ) != str(run["run_attempt"]):
            raise ValueError("Candidate evidence run identity mismatch")
        if run["conclusion"] != "success":
            report["status"] = "unhealthy"
        observations.append(report)
    failures = evaluate_reports(observations, tag, sha, now, published)
    if failures:
        raise ValueError("; ".join(failures))
    print(f"Stable promotion gate passed for {tag} ({sha})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()
    stable = re.fullmatch(r"refs/tags/v([0-9]+\.[0-9]+\.[0-9]+)", args.ref)
    if not stable:
        print("Candidate observation gate applies only to stable core tags")
        return
    config = json.loads(Path(".github/release-candidate.json").read_text(encoding="utf-8"))
    check_remote(args.repo, config, stable.group(1))


if __name__ == "__main__":
    main()
