"""Verify core alone, upgrades, CLI behavior, then optional plugin installation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    parser.add_argument("--core-version", help="install this exact published PyPI version")
    parser.add_argument("--plugin-version", default="0.1.0")
    parser.add_argument(
        "--previous-core-version", help="test an upgrade from this PyPI version"
    )
    args = parser.parse_args()
    if args.core_version:
        core = f"swe-internship-scraper=={args.core_version}"
        plugin = f"swe-scraper-icims=={args.plugin_version}"
        version = args.core_version
    else:
        core_path = next(args.directory.glob("swe_internship_scraper-*.whl")).resolve()
        core = str(core_path)
        plugin = str(next(args.directory.glob("swe_scraper_icims-*.whl")).resolve())
        version = core_path.name.split("-")[1]
    behavior = Path(__file__).with_name("smoke_behavior.py").resolve()
    with tempfile.TemporaryDirectory() as directory:
        working = Path(directory)
        environment = working / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        executable = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)

        def run(*arguments):
            subprocess.run(
                [str(executable), "-I", *arguments], cwd=working, env=env, check=True
            )

        def install(requirement, retry=False):
            for attempt in range(6 if retry else 1):
                try:
                    run(
                        "-m",
                        "pip",
                        "--isolated",
                        "install",
                        "--no-input",
                        "--index-url",
                        "https://pypi.org/simple",
                        requirement,
                    )
                    return
                except subprocess.CalledProcessError:
                    if not retry or attempt == 5:
                        raise
                    time.sleep(15)

        if args.previous_core_version:
            install(f"swe-internship-scraper=={args.previous_core_version}")
            run(
                "-c",
                "import swe_scraper; "
                f"assert swe_scraper.__version__ == {args.previous_core_version!r}",
            )
        install(core, retry=bool(args.core_version))
        run("-c", f"import swe_scraper; assert swe_scraper.__version__ == {version!r}")
        run("-m", "swe_scraper", "--version")
        run(
            "-c",
            "import importlib.util; "
            "from swe_scraper.providers.registry import DEFAULT_REGISTRY; "
            "assert 'icims' not in DEFAULT_REGISTRY.names; "
            "assert importlib.util.find_spec('swe_scraper.compat') is None",
        )
        run(str(behavior))
        install(plugin, retry=bool(args.core_version))
        run(
            "-c",
            "from swe_scraper.providers.registry import DEFAULT_REGISTRY; "
            "assert 'icims' in DEFAULT_REGISTRY.names",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
