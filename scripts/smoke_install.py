"""Install built core and plugin wheels into a clean environment."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args()
    core = next(args.directory.glob("swe_internship_scraper-*.whl"))
    plugin = next(args.directory.glob("swe_scraper_icims-*.whl"))
    with tempfile.TemporaryDirectory() as directory:
        environment = Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        run([str(python), "-m", "pip", "install", str(core), str(plugin)])
        run([str(python), "-m", "swe_scraper", "--version"])
        help_text = subprocess.check_output(
            [str(python), "-m", "swe_scraper", "scan", "--help"],
            text=True,
        )
        for option in ("--quick", "--strict", "--include-adjacent"):
            if option not in help_text:
                raise RuntimeError(f"installed scraper is missing the {option} option")
        run(
            [
                str(python),
                "-c",
                (
                    "import importlib.util; import swe_scraper_icims; "
                    "from swe_scraper.providers.registry import DEFAULT_REGISTRY; "
                    "assert 'icims' in DEFAULT_REGISTRY.names; "
                    "assert importlib.util.find_spec('swe_scraper.compat') is None"
                ),
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
