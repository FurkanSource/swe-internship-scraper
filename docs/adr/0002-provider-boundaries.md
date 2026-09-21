# ADR 0002: API core and experimental HTML plugins

- Status: Accepted
- Date: 2026-09-21

## Decision

Providers backed by stable public ATS APIs may ship in the core package after contract and canary gates. HTML portals ship as independently versioned experimental plugins.

## Consequences

The core support promise covers six providers. iCIMS markup changes can be released through `swe-scraper-icims` without changing the core package. Every HTML plugin remains same-origin and public-only.
