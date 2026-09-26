# Changelog

This project follows Semantic Versioning.

## [Unreleased]

## [1.0.0rc3] - 2026-09-26

### Added

- Full CI and security checks on each release commit before publishing.
- Core-only and plugin clean-install behavior checks on Windows, Linux, and macOS.
- Exact-version PyPI installation and upgrade verification after publication.
- Candidate-specific daily/weekly health evidence and a fail-closed stable promotion gate.
- Release roadmap, recovery procedures, and explicit scan scope/limitations.

### Fixed

- Preserve explicit location and keyword constraints with `--all-jobs`.
- Honor empty Workday searches and normalize site slashes.
- Parse compact calendar dates before Unix timestamps.
- Join transitive exact identities while preventing uncertain semantic bridges.
- Require explicit custom canary profiles and clarify profile selection errors.
- Audit top-level catalog origins and safely display Unicode on legacy consoles.
- Treat valid empty boards as healthy and reject malformed fetch payloads.
- Fetch SmartRecruiters and Oracle details concurrently with bounded workers;
  retry total drift once from the beginning while preserving completeness checks.
- Reject incomplete, repeated, malformed, or inconsistent Workday pagination.
- Enforce response size limits while streaming JSON and HTML, and release connections.
- Publish core and iCIMS independently with version-checked `v*` and `icims-v*` tags.

### Changed

- Focus default matching on software internships; use `--include-adjacent` for
  broader technical roles such as analytics and research.
- Add opt-in `--strict` to return a failure status for partial scans and stop watch
  on provider errors, after saving available results.

## [1.0.0rc2] - 2026-09-25

### Added

- SmartRecruiters and Oracle core providers with fail-closed pagination.
- Experimental `swe-scraper-icims` entry-point plugin.
- Schema v2 source provenance and structured merge evidence with schema v1 reads.
- Deterministic, season-aware deduplication and optional uncertainty reports.
- Per-target health states, provider quorum, 18 daily canaries, and verified catalog expansion.
- A 350-pair sanitized deduplication benchmark with zero-false-merge release gate.
- Python 3.10 through 3.14 quality, packaging, security, provenance, and release workflows.
- A `--quick` sample scan and a short first-run path that does not require a checkout.

### Changed

- Renamed the transport contract to `HttpClient`; `JsonClient` remains an alias through 1.x.
- Runtime dependencies now use a compatible Requests range without a direct urllib3 pin.
- Prepared the package as a standalone public scraper with no tracker or applicant data.
- Corrected the PyPA publishing action pin. The `rc1` tag's upload failed before
  any PyPI package or GitHub Release was created.

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
