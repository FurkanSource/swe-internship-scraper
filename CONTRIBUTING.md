# Contributing

Contributions should preserve direct application URLs, fail visibly on incomplete data, and keep candidate-specific assumptions outside the package.

## Setup

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements-dev.lock -e .
```

## Required checks

```powershell
ruff format --check src tests plugins scripts
ruff check src tests plugins scripts
py -m mypy
py -m coverage run -m unittest discover -s tests -p "test_*.py"
py -m coverage run --append -m unittest discover -s plugins/icims/tests -p "test_*.py"
py -m coverage report
py -m build
py -m twine check dist/*
```

Provider changes require sanitized fixtures for normal, malformed, and incomplete pagination responses. Live checks belong in the scheduled health workflow rather than unit tests.

Pull requests should explain the contract change, include evidence, update configuration documentation, and confirm direct URLs. Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
