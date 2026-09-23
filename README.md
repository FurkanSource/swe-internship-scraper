# SWE Internship Scraper

[![CI](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/ci.yml)
[![Provider health](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/provider-health.yml/badge.svg)](https://github.com/FurkanSource/swe-internship-scraper/actions/workflows/provider-health.yml)
[![PyPI](https://img.shields.io/pypi/v/swe-internship-scraper.svg)](https://pypi.org/project/swe-internship-scraper/)

A Python library and CLI for collecting software engineering internships from official public applicant tracking system endpoints.

The project contains scraper code, provider fixtures, and a public employer catalog. It contains no application tracker, applicant profile, database, or personal ranking rules.

## Supported providers

| Provider | Source | Status |
| --- | --- | --- |
| Greenhouse | Public Job Board API | Supported |
| Lever | Public Postings API | Supported |
| Ashby | Public Posting API with public GraphQL fallback | Supported |
| Workday | Public candidate site API | Supported |
| SmartRecruiters | Public Posting API | Supported |
| Oracle Recruiting | Public Candidate Experience API | Supported |
| iCIMS | Public HTML and JSON-LD | Experimental plugin |

The bundled catalog contains 1,304 live-verified boards. Each core provider has three daily canaries. The project never signs in, solves CAPTCHAs, or bypasses access controls.

## Install

Python 3.10 through 3.14 is supported.

```powershell
py -m pip install swe-internship-scraper
swe-scraper providers
swe-scraper scan --output jobs.json
```

For a source checkout:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -e .
```

The optional iCIMS plugin is released separately:

```powershell
py -m pip install swe-scraper-icims
swe-scraper providers
```

## Commands

```powershell
swe-scraper scan --output jobs.json
swe-scraper scan --providers greenhouse,oracle --output jobs.json
swe-scraper scan --target-set all --max-workers 12 --output all-jobs.json
swe-scraper scan --dedupe-report dedupe-audit.json --output jobs.json
swe-scraper validate jobs.json
swe-scraper watch --interval 21600 --notify-jsonl events.jsonl
swe-scraper health --output provider-health.json
```

Health exits `0` when every target passes, `1` when each provider still meets quorum with at least one failed target, and `2` for a provider quorum, contract, or pagination failure.

## Output and deduplication

Schema v2 keeps the familiar primary job fields and adds:

- `sources`: every provider, source ID, and direct application URL represented by the record;
- `merge_evidence`: the rule and confidence for each merged source.

Exact canonical URLs and provider/source identities have confidence `1.0`. Semantic merging requires canonical company, equivalent title including season and year, and equivalent location. Uncertain pairs remain separate and can be written to the optional deduplication audit.

Primary record selection uses completeness and stable lexical tie breakers, so thread completion order does not change output. See [the schema reference](docs/output-schema.md) and [the v2 migration guide](docs/schema-v2-migration.md).

## Provider targets

Custom target files use the same shape as the bundled catalog:

```json
{
  "smartrecruiters": [
    {"name": "Example", "slug": "company-identifier"}
  ],
  "oracle": [
    {
      "name": "Example",
      "slug": "CX_1",
      "origin": "https://example.fa.us2.oraclecloud.com"
    }
  ]
}
```

Invalid target options fail before any network request. Run `swe-scraper providers --json` to inspect installed providers and required fields.

## Development

```powershell
py -m pip install -r requirements-dev.lock -e .
ruff format --check src tests plugins scripts
ruff check src tests plugins scripts
py -m mypy
py -m coverage run -m unittest discover -s tests -p "test_*.py"
py -m coverage run --append -m unittest discover -s plugins/icims/tests -p "test_*.py"
py -m coverage report
```

Read [CONTRIBUTING.md](CONTRIBUTING.md), [the provider guide](docs/provider-development.md), and [the support policy](SUPPORT.md) before opening an issue or pull request.

## License

MIT
