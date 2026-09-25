# Changelog

This project follows Semantic Versioning.

## [Unreleased]

## [1.0.0rc1] - Pending

### Added

- SmartRecruiters and Oracle core providers with fail-closed pagination.
- Experimental `swe-scraper-icims` entry-point plugin.
- Schema v2 source provenance and structured merge evidence with schema v1 reads.
- Deterministic, season-aware deduplication and optional uncertainty reports.
- Per-target health states, provider quorum, 18 daily canaries, and verified catalog expansion.
- A 350-pair sanitized deduplication benchmark with zero-false-merge release gate.
- Python 3.10 through 3.14 quality, packaging, security, provenance, and release workflows.

### Changed

- Renamed the transport contract to `HttpClient`; `JsonClient` remains an alias through 1.x.
- Runtime dependencies now use a compatible Requests range without a direct urllib3 pin.
- Prepared the package as a standalone public scraper with no tracker or applicant data.

## [0.2.0] - 2026-09-20

### Added

- Canonical target catalog with priority, all, and canary profiles.
- Conservative semantic deduplication with auditable confidence metadata.
- Runtime and entry-point provider registration.
- JSON Lines discovery notifications and the `health` command.
- Sanitized provider contract fixtures, daily canaries, and tagged release builds.

### Changed

- Greenhouse, Lever, Ashby, and Workday providers share one package contract.
- Release builds verify that private consumer modules cannot enter the public wheel.
- Package version advanced to `0.2.0` for the CLI and catalog changes.

### Removed

- Duplicate `tools/targets.json` and `tools/all_targets.json` catalogs.

## [0.1.0] - 2026-09-20

### Added

- Public package and CLI with Greenhouse, Lever, Ashby, and Workday providers.
- JSON and CSV export, validation, watch state, documentation, and CI.
