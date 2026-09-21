# ADR 0003: Provenance and deterministic deduplication

- Status: Accepted
- Date: 2026-09-21

## Decision

Schema v2 makes all source identities and merge evidence first-class. Deduplication sorts inputs, uses exact identities before semantic rules, preserves season and year, and selects a primary record with stable completeness and lexical keys.

## Consequences

Output no longer depends on worker completion order. Exact matches have confidence `1.0`; conservative semantic matches have confidence `0.93`. Uncertain pairs remain separate and may be exported for review. Readers continue to accept v1.
