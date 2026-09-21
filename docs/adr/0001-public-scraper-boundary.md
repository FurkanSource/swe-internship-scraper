# ADR 0001: Standalone public scraper

- Status: Accepted
- Date: 2026-09-21

## Decision

Publish the scraper as an independent repository and Python distribution. Application tracking, applicant profiles, databases, and legacy conversion remain in a private consumer.

## Consequences

The public build uses an explicit allowlist and begins with clean history. The private consumer pins a released package version. Public artifacts can be audited without interpreting tracker code or personal state.
