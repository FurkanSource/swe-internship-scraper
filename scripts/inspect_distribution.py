"""Fail when built distributions cross the public scraper boundary."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

DENIED_PARTS = {
    "compat.py",
    "tracker",
    "tools",
    "applications.db",
    "personal.json",
    "applicant.json",
}


def members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def inspect(path: Path) -> None:
    names = members(path)
    lowered = [name.casefold().replace("\\", "/") for name in names]
    denied = [
        name for name in lowered if any(part in name.split("/") for part in DENIED_PARTS)
    ]
    if denied:
        raise RuntimeError(f"private files found in {path.name}: {denied[:10]}")
    if "swe_internship_scraper" in path.name:
        if not any(name.endswith("swe_scraper/data/targets.json") for name in lowered):
            raise RuntimeError(f"{path.name} omitted the canonical target catalog")
        if not any(name.endswith("swe_scraper/py.typed") for name in lowered):
            raise RuntimeError(f"{path.name} omitted py.typed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args()
    artifacts = sorted(args.directory.glob("*.whl")) + sorted(
        args.directory.glob("*.tar.gz")
    )
    if not artifacts:
        raise RuntimeError("no distributions found")
    for artifact in artifacts:
        inspect(artifact)
        print(f"OK {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
