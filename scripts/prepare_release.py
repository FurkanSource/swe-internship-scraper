"""Select a single package's distributions and validate its release tag."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

PACKAGES = {"core": "swe_internship_scraper", "icims": "swe_scraper_icims"}


def prepare_release(
    distributions: Path, destination: Path, ref: str, package: str = "core"
) -> str:
    version = None
    if ref.startswith("refs/tags/icims-v"):
        package, version = "icims", ref.removeprefix("refs/tags/icims-v")
    elif ref.startswith("refs/tags/v"):
        package, version = "core", ref.removeprefix("refs/tags/v")
    elif ref.startswith("refs/tags/"):
        raise ValueError(f"unsupported release tag: {ref}")
    if package not in PACKAGES:
        raise ValueError(f"unsupported release package: {package}")
    prefix = PACKAGES[package]
    wheels = sorted(distributions.glob(f"{prefix}-*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected exactly one {package} wheel, found {len(wheels)}")
    wheel = wheels[0]
    built_version = wheel.name.split("-")[1]
    if version is not None and version != built_version:
        raise ValueError(
            f"tag version {version!r} does not match wheel version {built_version!r}"
        )
    sdist = distributions / f"{prefix}-{built_version}.tar.gz"
    if not sdist.is_file():
        raise ValueError(f"missing matching source distribution: {sdist.name}")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("release destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    for path in (wheel, sdist):
        shutil.copy2(path, destination / path.name)
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distributions", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--package", choices=tuple(PACKAGES), default="core")
    args = parser.parse_args()
    package = prepare_release(args.distributions, args.destination, args.ref, args.package)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"package={package}\n")
    print(f"Prepared {package} distributions in {args.destination}")


if __name__ == "__main__":
    main()
