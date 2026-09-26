"""Record live health tied to an immutable release candidate and Actions run."""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--checkout", type=Path, default=Path("candidate"))
    args = parser.parse_args()
    config = json.loads(Path(".github/release-candidate.json").read_text(encoding="utf-8"))
    version = config["version"]
    if (
        not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+rc[0-9]+", version)
        or config["tag"] != f"v{version}"
    ):
        raise ValueError("Candidate must have matching version and immutable rc tag")
    if args.select:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
            handle.write(f"tag={config['tag']}\n")
        return 0
    checkout = args.checkout.resolve()
    output = Path("build/candidate-health").resolve()
    output.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("OBSERVATION_MODE", "daily")
    if mode not in {"daily", "weekly"}:
        raise ValueError("Observation mode must be daily or weekly")
    sha = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    installed = subprocess.check_output(
        [sys.executable, "-m", "swe_scraper", "--version"], cwd=checkout, text=True
    ).strip()
    if installed != f"swe-scraper {version}":
        raise ValueError(f"Installed candidate version mismatch: {installed}")
    command = [sys.executable]
    if mode == "weekly":
        command += [str(checkout / "scripts/weekly_health.py"), "--per-provider", "10"]
    else:
        command += ["-m", "swe_scraper", "health", "--max-workers", "12"]
    health_path = output / "health.json"
    result = subprocess.run([*command, "--output", str(health_path)], cwd=checkout)
    payload = (
        json.loads(health_path.read_text(encoding="utf-8"))
        if health_path.exists()
        else {
            "status": "unhealthy",
            "checks": [],
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    )
    payload.update(
        candidate_tag=config["tag"],
        candidate_sha=sha,
        version=version,
        mode=mode,
        run_id=os.environ["GITHUB_RUN_ID"],
        run_attempt=os.environ["GITHUB_RUN_ATTEMPT"],
        exit_code=result.returncode,
    )
    (output / "stability.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
