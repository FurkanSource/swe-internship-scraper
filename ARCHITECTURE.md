# Architecture

```text
official public ATS endpoints
            |
        HttpClient
            |
 provider registry + validated Target
            |
       normalized Job
            |
     filters -> deterministic dedupe -> exporters / watch / health
```

## Boundaries

The public repository owns scraping, normalization, filtering, deduplication, health checks, export, fixtures, and the verified target catalog. Applicant data and application tracking remain in a separate private consumer that depends on the released package.

API-backed providers are part of the core package. HTML providers are released as experimental entry-point plugins so portal-specific markup changes do not weaken the core support contract.

## Failure model

Targets validate before workers or network requests start. A board failure is returned as a structured `ProviderFailure`; healthy boards are retained. Pagination adapters track stable IDs, totals, cursors, and hard page ceilings. They raise instead of returning a known partial result.

## Identity model

Every job carries one or more `JobSource` values. Exact URL and source identity matches merge with confidence `1.0`. A semantic merge requires canonical company, equivalent title with explicit term and year, and overlapping normalized location. The selected primary record depends on completeness and stable lexical tie breakers.

See the decisions in [docs/adr](docs/adr).
