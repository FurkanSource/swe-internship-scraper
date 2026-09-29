# Major improvements: evidence and limits

Reviewed September 28, 2026, after integrating the throughput and filter/CSV PRs.
These are development changes after `1.0.0rc3`, not a published release.

## Changes and measured benefit

| Change | Evidence | Limit |
| --- | --- | --- |
| Workday page ceilings | Recorded 401-row board completes in 21 requests with `max_pages=25`; low ceilings still fail. Live Moog returned 485 listings over 25 pages. | More complete listing scope can take longer. |
| Workday query scope | Unspecified search now uses full listings; explicit scopes are preserved. Co-op fixture stays present. Four shared live boards retained all 12 qualifying IDs and direct URLs versus merged main. | No additional qualifying IDs were observed in that four-board comparison. Unfiltered backend totals do not prove global employer coverage. |
| Useful Workday details | Location/keyword constraints request plausible candidate details. Additional locations and duties pass filter regressions. Unconstrained scans make no Workday detail requests. | `--all-jobs` with these constraints needs every posting's details. |
| Missing employers | Autodesk and GE Aerospace returned 9 and 14 matching roles in the final priority scan; their source-linked control postings were retained. HPE returned 13 matches in the first scan. | HPE later failed malformed-row validation. Its board is not consistently complete. GM and Medtronic additions were deferred after failed full scans. |
| Optional detail cache | Five unchanged paced replays: 3000 detail calls versus zero; median 6.013s versus 2.792s, 53.57% lower. Changed/new rows fetched 11 details; deleted IDs stayed absent. | Synthetic pacing is batched. First cached replay was slower. No cold full-catalog speedup claim. |
| Live cache check | Workiva, Kioxia and Metrolinx: cold 4.007s/18 details; warm 2.645s/0 details; refresh 3.709s/18 details. All three phases retained identical qualifying IDs and URLs, without failures. Listing requests remained fresh. | One bounded sample, not a repeated live median or full-catalog benchmark. Description-only changes can lag until explicit TTL expiry. |

Workday rechecks an otherwise structured page with invalid rows once. It never
discards those rows to return successful partial results. Repeated rows, changed
totals, incomplete pages and persistent malformed rows still fail.

## Final verification

- 196 public unit/contract tests and 6 experimental plugin tests passed.
- Ruff format/lint and strict mypy passed; combined coverage was 93%.
- Core and plugin wheel/sdist builds, content inspection, metadata validation
  and isolated Windows installation/CLI smoke tests passed locally.
- Allowlisted staging audit passed boundary, secret/PII pattern, licensing,
  catalog URL and distribution checks. This is not a guarantee that regex scans
  detect every possible secret.
- Fresh provider health passed all 18 canaries across all six providers.
- An independent bounded review found a supported Oracle title alias rejected
  by the new validator. It was corrected and tested with and without caching.
- Shared source/tests were synchronized into the private consumer, keeping its
  converter private. Private verification passed 196 package and 314 workflow
  tests; the real application database hash remained unchanged.

## Current operating result

The final priority scan returned **216 internships with nine board failures**
across 76 configured priority boards. The catalog has 1298 boards in total;
no full-catalog before/after run was performed.

Failures were five Ashby boards returning a null board, Applied Intuition's
Greenhouse slug returning 404, Anduril's response exceeding the bounded 10 MiB
limit, HPE's malformed Workday rows, and TD's repeated Workday pagination.
Earlier passes failed on different Workday pages; availability and consistency
remain variable. Passing canaries do not establish catalog-wide health.

## Assessment and remaining gates

**8/10 for an English-language SWE internship CLI.** This is an engineering
judgment based on useful results, correctness, speed, usability and maintainability,
not a coverage percentage or certification.

| Area | Rating | Reason |
| --- | ---: | --- |
| Correctness and tests | 9/10 | Deterministic output, strict contracts, meaningful regressions and strong gates. |
| Useful coverage | 7.5/10 | Six providers and verified new roles; stale slugs, inconsistent boards and unsupported employers remain. |
| Efficiency | 8/10 | Candidate fetching, audit avoidance, resume and optional measured detail reuse; full listings still dominate some boards. |
| Usability | 8/10 | Package installation, exports and recovery are straightforward; broad scans and failure interpretation still need care. |
| Maintainability | 7.5/10 | Shared normal orchestration is improved, but private package/test copies remain until stable migration. |

The private direct-board monitor now delegates its normal three-provider sweep
to the shared scanner. Its board stats describe raw listings before private
eligibility conversion. Legacy injected transport helpers remain for tested
compatibility. The private Workday facade honors configured scope and page
ceilings while retaining historical record and cycle conversion.

Before stable release: merge verified changes, publish a fresh candidate, satisfy
the existing seven-day observation and weekly sample/install gates, then migrate
the private consumer to the independently verified stable wheel. Existing rc3
observations do not cover changed provider behavior. Full private-copy removal
is deferred because a stable wheel is not yet available. Cancellation/watch
identity changes and medium/minimal cleanup are outside this major-only work.
