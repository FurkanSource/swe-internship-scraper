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

### Additive identity and recovery fields

Each source may now include `namespace`, an opaque provider-specific tenant/board
scope. An absent or empty namespace means unknown scope: match by exact canonical
URL only. Never compare raw requisition IDs across boards. IDs and namespaces
are case sensitive; provider names are case insensitive.

`--allow-partial` exports verified records from incomplete boards. These records
include `metadata.board_complete: false`; the board still appears in `errors`,
with `partial: true` when any rows were retained and a `details` list containing
`stage`, `source_id`, and `error`. Normal successful output and existing primary
fields remain compatible. CSV is a simplified view; use JSON for recovery details.

Historical semantic merges stay readable, but their separate original records
cannot be reconstructed reliably. A fresh scan restores separate requisitions.
