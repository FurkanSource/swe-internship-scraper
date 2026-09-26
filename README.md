# SWE Internship Scraper

[![CI](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/ci.yml)
[![Provider health](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/provider-health.yml/badge.svg)](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/provider-health.yml)
[![PyPI](https://img.shields.io/pypi/v/swe-internship-scraper.svg)](https://pypi.org/project/swe-internship-scraper/)

A command-line tool for finding software engineering internships on official public job boards.

## Quick start

**You do not need to clone or download this repository to use the scraper.** Install
the package with Python 3.10–3.14, then run a small sample scan. The CSV file is
written to the folder where you run the command.

Windows PowerShell:

```powershell
py -m pip install --pre swe-internship-scraper
py -m swe_scraper scan --quick --output internships.csv
```

macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --pre swe-internship-scraper
.venv/bin/python -m swe_scraper scan --quick --output internships.csv
```

Open `internships.csv` in a spreadsheet. `--quick` checks the small set of monitored
boards; it is a sample, not the full catalog. Omit `--quick` for the default
priority boards, or use `--target-set all` for the entire catalog. The `--pre`
install flag is needed while the first release is a release candidate; it can be
removed for stable `1.0.0`.

Want to run the source code you downloaded from GitHub? Follow the
[source checkout guide](docs/getting-started.md). It has exact Windows and
macOS/Linux commands, without requiring PowerShell activation.

## What you install

`swe-internship-scraper` is the main package. It includes the command and six
providers: Greenhouse, Lever, Ashby, Workday, SmartRecruiters, and Oracle.
The separate `swe-scraper-icims` package is an **optional, experimental** plugin
for public iCIMS portals. Most users only need the main package. See the
[plugin guide](docs/icims-plugin.md) if you have an iCIMS portal to scan.

The repository contains scraper code and a public employer catalog. It does not
include an application tracker, personal data, or applicant ranking rules.

## Commands

```powershell
py -m swe_scraper scan --output internships.csv
py -m swe_scraper scan --location "New York" --output nyc-internships.csv
py -m swe_scraper scan --target-set all --output all-internships.csv
py -m swe_scraper providers
py -m swe_scraper --help
```

The installed `swe-scraper` command is equivalent to `py -m swe_scraper` on
Windows or `python3 -m swe_scraper` in a Python environment on macOS/Linux.
For JSON output, use a `.json` filename. See the
[full command guide](docs/command-line.md) for custom targets, watching, health
checks, and deduplication reports.

## Output and deduplication

Schema v2 keeps the familiar primary job fields and adds:

- `sources`: every provider, source ID, and direct application URL represented by the record;
- `merge_evidence`: the rule and confidence for each merged source.

Exact canonical URLs and provider/source identities have confidence `1.0`. Semantic merging requires canonical company, equivalent title including season and year, and equivalent location. Uncertain pairs remain separate and can be written to the optional deduplication audit.

Primary record selection uses completeness and stable lexical tie breakers, so thread completion order does not change output. See [the schema reference](docs/output-schema.md) and [the v2 migration guide](docs/schema-v2-migration.md).

## Development

Read [CONTRIBUTING.md](CONTRIBUTING.md), [the provider guide](docs/provider-development.md),
and [the support policy](SUPPORT.md) before opening an issue or pull request.

## License

MIT
