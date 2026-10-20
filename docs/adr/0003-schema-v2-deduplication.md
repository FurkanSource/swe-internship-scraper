# ADR 0003: Provenance and deterministic deduplication

- Status: Accepted
- Date: 2026-09-21

## Decision

Schema v2 makes source identities and merge evidence first-class. The September
2026 audit supersedes automatic semantic merging: company, title and location
similarity alone does not establish that two postings share a requisition.

Canonical exact URLs and `(provider, namespace, source_job_id)` links form
connected groups, including identities already present in `sources`.
`namespace` is an optional additive field describing the provider's tenant/board
scope. Raw source IDs are case sensitive. Readers accept older v1/v2 records;
an unknown namespace permits URL-based merging only. Providers declare namespaces
explicitly rather than inferring them from company names.

Similarity remains available through the optional audit, preserving separate
requisitions and explicit remote country restrictions. Primary selection ranks
original records by completeness and stable lexical tie breakers, independent of
input or worker completion order.

## Consequences

Automatic matches have confidence `1.0`; similarity scores are review hints,
not calibrated probabilities. The 350-pair synthetic identity regression suite
checks 14 pattern families. Its pass rate does not establish real-world duplicate
recall or false-merge prevalence. Old exports with semantic merges cannot be
reliably separated from their primary fields alone; rerun the scan for fresh rows.
