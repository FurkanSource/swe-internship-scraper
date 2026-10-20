# Scan reliability and watch identity: implementation evidence

Date: September 30, 2026. Local candidate: `1.0.0rc5`. Baseline revision: `38016bad83070c680599f460d18586e0c7f07427`.

## Changes

- CLI `scan` recovers validated records by default. `--complete-boards-only` opts out; watch/library defaults remain complete-board behavior. The two recovery flags are mutually exclusive.
- Scan statuses report complete success (0), failures with exported jobs (1), failures without jobs or export/configuration failures (2), and interruption (130). `--strict` does not abort scans mid-run. Recurring watch stops on provider failures only with `--strict`.
- Listing and detail deadlines retain only completed, validated jobs in listing order. Global cancellation takes precedence, including during detail-worker shutdown. Interrupted details never fall back to unverified summaries for description/location filters.
- Workday rechecks one malformed page, retains valid rows, and continues later pages. Incomplete records retain `board_complete=false` through deduplication and never become successful board checkpoints.
- Watch stores every exact source/URL alias and learns aliases from already-seen merged jobs. Known primary-source changes no longer repeat alerts. Historical state keys remain readable.
- Health treats malformed/incomplete listing contracts as release-blocking failures even when sibling canaries meet quorum.

## Verification

- Core: **313 tests**; plugin: **6 tests**. Private copy: **313 core**, **6 plugin**, and **54 tracker tests**. All passed.
- Combined line coverage: **96.40%**; combined line/branch score: **94.83%**; branch coverage: **89.83%**. The existing 90% package gate passed. This does not claim every existing branch is covered.
- Ruff lint/format and strict mypy (33 source files) passed for both copies. The private compatibility converter was preserved.
- Core/plugin wheel and sdist builds, distribution boundary inspection, wheel-content checks, and Twine metadata checks passed.
- A fresh isolated Windows environment installed published `1.0.0rc4`, upgraded to the local `1.0.0rc5` wheel, passed offline CLI/export/watch/error smoke checks, and discovered the installed iCIMS plugin. No private converter was present in the public wheel.
- The dependency audit of the runtime requirements and locked development environment reported no known vulnerabilities.
- All **34 shared package files** match byte for byte between staged public code and the private copy; the core wheel matches those source files. The real application database's SHA-256 is unchanged.

## Measured recovery benefit

Five deterministic replays per case used a 60-job Workday board with a 20-row page size. Healthy scans preserved all 60 identities and URLs with three listing calls. A malformed middle row previously retained 39 valid jobs with three calls; the new code retains **59** with four calls, omits the malformed job, marks every retained record incomplete, and reports a board failure. The extra call retrieves the last page. This is a coverage/recovery improvement, not a speedup claim.

## Bounded live comparison

One before/after sample used the same target configurations, recovery option, candidate filters, and 20-second cooperative board budget. All four targets passed in the final sample; qualifying job IDs and direct URLs matched exactly. Counts are logical client calls; internal HTTP retries are excluded.

| Target | Qualifying jobs | Listing calls before / after | Detail calls before / after | Seconds before / after |
| --- | ---: | ---: | ---: | ---: |
| Cloudflare (greenhouse) | 1 | 1 / 1 | 0 / 0 | 0.900 / 2.052 |
| Workiva (workday) | 2 | 6 / 6 | 0 / 0 | 3.109 / 2.971 |
| Kioxia (smartrecruiters) | 0 | 1 / 1 | 0 / 0 | 0.469 / 0.719 |
| Duracell (oracle) | 0 | 2 / 2 | 6 / 6 | 3.111 / 2.407 |

These sequential samples are too small and affected by network variation to establish a catalog-wide latency change. Empty qualifying results do not prove coverage of every role or provider.

### A review recommendation disproved by the live check

The independent reviewer proposed rejecting an Oracle page when `hasMore=false` appeared before `TotalJobsCount`. An intermediate implementation failed the Duracell canary. Captured public responses showed 29 requisitions: 25 at offset 0 and four at offset 25, with both root and nested flags false in each response. The recommendation was removed. A regression now preserves both pages, all 29 identities, and their direct URLs. Consistent reported totals continue to govern traversal; repeated pages, malformed responses, overshoot, unresolved drift, and ceilings still fail closed.

Oracle documents separate finder `limit`/`offset` variables and collection metadata; the adapter decision above is based on the observed public response, not an assumption that every false flag ends finder pagination. [Oracle REST reference](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingicejobrequisitions-get.html).

## Delivery and release boundary

Implementation and local Windows checks are complete. The candidate is prepared locally. Remote CI on Python 3.10-3.14 and Linux, candidate publication, a fresh seven-day healthy observation, and the weekly sample remain release gates. This report does not claim those have run or that a stable release has been published.
