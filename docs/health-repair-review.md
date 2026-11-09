# Health repair review — November 9, 2026

Three bounded Ralph reviews used the same acceptance criteria: address the
observed workflow/catalog failures, preserve completeness and cancellation,
and verify the result without weakening health gates.

## Pass 1: Correct diagnosis and bounded recovery

- Matched CodeQL init and analyze SHAs; grouped their Dependabot updates.
- Separated malformed detail contracts from HTTP failures. An HTTPS URL in an
  exception is not evidence of an HTTP error.
- Retry malformed detail objects once. Network exceptions retain the HTTP
  client's existing retry policy. Invalid responses are never cached.
- Count every detail request attempt, and check cancellation before and after
  each fetch. Persistent invalid responses remain errors.

## Pass 2: Coverage and record identity

- Workday retries a malformed listing page once, as before. A title still
  missing can then be recovered from that row's validated direct job path.
- If the listing provides a requisition ID, detail recovery requires the same
  ID. Missing paths and mismatched identities are not guessed or ignored.
- Corrected an edge case where a failed lookup could discard valid neighbors
  in partial mode. It now retains them with explicit record issues. Strict
  complete-board scans still fail.
- Migrated the verified GlossGenius board. Documented Sanctuary's unsupported
  Kula migration and Firetiger's unavailable board in catalog-maintenance.md.

## Pass 3: Delivery and independent checks

- Corrected the new catalog test's implicit Windows encoding to UTF-8.
- 328 core tests and 6 plugin tests passed; combined coverage 95%.
- Ruff checks, formatting, and strict mypy passed.
- All 18 live canaries passed, including Salesforce and Duracell. The migrated
  GlossGenius board returned 17 jobs through the actual Ashby adapter.
- Existing immutable rc6 observations must not be relabeled as tests of these
  changes. A fresh release candidate and observation window are necessary.

No implementation can guarantee that third-party boards remain available.
Malformed rows without a recoverable direct path still produce a failure;
this protects coverage and prevents false closure decisions downstream.

## Expanded live verification

GitHub CI and Security passed for implementation commit `979693e`, including
Python 3.10-3.14 on Windows/Linux and clean installs on Windows/Linux/macOS.
Local wheel/sdist inspection and clean-install smoke tests also passed.

The `2026-W46` rotation completed with **57/60 healthy boards**. It must not be
reported as a passing weekly run. Cadence and Leidos returned malformed Workday
rows on that run; fresh full-board checks subsequently passed with 695 and
2,000 jobs respectively. Those rechecks do not erase the original failures.

Cohu remains an upstream pagination inconsistency: its Oracle response reports
197 jobs, but the first requested 25-row page contains only 24. The next offset
computed from returned rows repeats an ID. An experimental explicit posting-date
sort also stopped before the reported total, so changing sort order did not
establish completeness. The board stays in the catalog and its failure remains
visible; dropping a live board or accepting an unproven count would conceal a
coverage gap. There is no verified local correction for that response yet.

The existing health workflow therefore can still fail on real upstream
inconsistencies. A fresh candidate and clean observation window remain release
requirements; this repair PR does not claim release readiness.
