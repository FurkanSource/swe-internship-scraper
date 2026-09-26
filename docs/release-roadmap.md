# Roadmap to stable 1.0

Release readiness means reproducible installation and verified behavior within
the documented English-language, six-provider scope. It does not mean complete
coverage of every employer or country.

| Phase | Work | Completion evidence |
| --- | --- | --- |
| 1. Integrate | Merge the verified scraper fixes (PR #7). | Green CI on the merged commit. |
| 2. Enforce | Run reusable CI and security workflows inside every release; gate stable core publishing on candidate evidence. | A failing check blocks publishing for the exact tagged commit. |
| 3. Exercise | Test core alone, plugin discovery, offline scan/export/watch/error behavior, and Windows/Linux/macOS clean installs. | Clean environments outside the source checkout pass. |
| 4. Candidate | Publish a new immutable rc3 tag with changelog, checksums, SBOM, and provenance. | Exact-version PyPI installation and upgrade tests pass on all three OSes. |
| 5. Observe | Run daily and weekly health against the candidate tag, not a moving main branch. | Seven consecutive healthy UTC observation dates and a healthy 60-target weekly sample for the same commit. |
| 6. Promote | Change only core version metadata and changelog; publish 1.0.0. | Automated stability gate, release checks, and post-publication installation pass. |

## Stop conditions

- Do not promote based on repeat runs from one day, green main-branch canaries,
  an expired artifact, or an incomplete health report.
- A failed observation invalidates the affected day. A behavior change requires
  a new candidate tag and a new observation period.
- A missing published package or environment approval blocks publication; record
  the blocker without weakening checks or reusing a version.
- The seven-day observation period cannot be completed during one implementation
  session. Candidate observation runs are scheduled by GitHub Actions.

See [release operations](release-operations.md) for publishing and recovery.
