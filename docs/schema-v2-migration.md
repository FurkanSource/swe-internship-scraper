# Schema v2 migration

Schema v2 adds provenance while preserving every v1 primary job field.

## Reader migration

Readers should:

1. accept `schema_version` 1 or 2;
2. use `sources` when present;
3. synthesize one source from `provider`, `source_job_id`, and `application_url` for v1;
4. treat `merge_evidence` as an empty list when absent.

## Writer behavior

The scraper writes v2. A single-source job has one `sources` entry and no merge evidence. A merged job retains one primary set of compatibility fields plus every represented source and structured evidence.

The legacy `metadata.deduplication` summary remains during 1.x for consumers that used the 0.x output.
