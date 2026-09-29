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

Targets validate before workers or network requests start. A board failure is returned as a structured `ProviderFailure`; healthy boards are retained. Pagination adapters track stable IDs, totals, cursors, and hard page ceilings. Strict fetch and health never return incomplete boards as successful. Explicit recovery scans may retain verified records carried by `PartialFetchError` with a typed `BoardFetchResult`, record issues, and pagination status. These boards remain errors and are retried on resume. Work submission is bounded; cancellation and optional deadlines propagate into provider detail threads and HTTP waits without changing the `fetch(target, client)` interface.

## Identity model

Every job carries one or more `JobSource` values. Canonical exact URLs and explicitly scoped `(provider, namespace, source_job_id)` identities merge with confidence `1.0`. Sources without a namespace merge by URL only. Similar company/title/location combinations remain separate and may appear in the optional audit. The selected primary record depends on original-record completeness and stable lexical tie breakers.

See the decisions in [docs/adr](docs/adr).
