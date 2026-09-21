"""Create the clean public repository tree from an explicit allowlist."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.lock",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "ARCHITECTURE.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "COMPATIBILITY.md",
)
DIRECTORIES = (".github", "docs", "plugins", "scripts", "tests")

PUBLIC_GITIGNORE = """\
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
build/
dist/
*.egg-info/
.swe-scraper-seen.json
jobs.json
jobs-latest.json
reports/*.json
"""


def ignored(path: Path) -> bool:
    return path.name in {"__pycache__", ".coverage"} or path.suffix in {".pyc", ".pyo"}


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=lambda root, names: [name for name in names if ignored(Path(root) / name)],
    )


def stage(destination: Path) -> None:
    resolved = destination.resolve()
    build_root = (ROOT / "build").resolve()
    if resolved != build_root and build_root not in resolved.parents:
        raise ValueError(
            "staging destination must be inside the repository build directory"
        )
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(ROOT / name, resolved / name)
    for name in DIRECTORIES:
        copy_tree(ROOT / name, resolved / name)
    shutil.rmtree(resolved / "docs" / "testing", ignore_errors=True)
    examples = resolved / "examples"
    examples.mkdir()
    shutil.copy2(ROOT / "examples" / "targets.example.json", examples)
    (resolved / ".gitignore").write_text(PUBLIC_GITIGNORE, encoding="utf-8")
    public_pyproject = resolved / "pyproject.toml"
    pyproject_text = public_pyproject.read_text(encoding="utf-8")
    pyproject_text = (
        pyproject_text.replace('exclude = ["swe_scraper.tracker*"]\n', "")
        .replace('exclude = ["src/swe_scraper/compat.py"]\n', "")
        .replace('omit = ["src/swe_scraper/compat.py"]\n', "")
    )
    public_pyproject.write_text(pyproject_text, encoding="utf-8")
    package = resolved / "src" / "swe_scraper"
    copy_tree(ROOT / "src" / "swe_scraper", package)
    (package / "compat.py").unlink(missing_ok=True)

    forbidden_roots = {"tools", "data", "config", ".git"}
    forbidden_names = {
        "compat.py",
        "applications.db",
        "personal.json",
        "applicant.json",
    }
    violations = [
        str(path.relative_to(resolved))
        for path in resolved.rglob("*")
        if path.relative_to(resolved).parts[0].casefold() in forbidden_roots
        or path.name.casefold() in forbidden_names
    ]
    if violations:
        raise RuntimeError(f"public staging boundary violation: {violations[:10]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "destination",
        type=Path,
        nargs="?",
        default=ROOT / "build" / "swe-internship-scraper",
    )
    args = parser.parse_args()
    stage(args.destination)
    print(args.destination.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
