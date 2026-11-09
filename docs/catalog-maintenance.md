# Catalog maintenance

The active catalog contains supported public boards. Removing an unavailable
board does not mean its employer has no jobs. Keep evidence here so future
maintenance can restore coverage without rediscovering old failures.

## November 9, 2026

| Employer | Previous target | Action and evidence |
| --- | --- | --- |
| GlossGenius | Greenhouse `glossgenius` | Migrated to Ashby `geniusai`, displayed as GlossGenius (Genius AI). The [old careers page](https://glossgenius.com/careers) redirects to [Genius AI](https://genius.ai/careers), whose application link points to this board. The supported public GraphQL endpoint returned 17 postings. |
| Sanctuary AI | Lever `sanctuary` | Retired the 404 target. [Employer careers](https://sanctuary.ai/careers/) now links to [Kula](https://careers.kula.ai/sanctuary-ai). Kula is not currently supported; these opportunities need manual discovery. |
| Firetiger | Ashby `firetiger` | Quarantined outside the active catalog after the November 1 CI failure and a fresh November 9 public GraphQL response with `jobBoard: null`. No verified replacement; recheck [the official board](https://jobs.ashbyhq.com/firetiger) before restoring. A local REST 403 alone was not used as proof of closure. |

These changes affect future main-branch scans. An immutable release candidate
continues to use its original catalog and provider code until a new candidate
is released and selected. Do not retag an existing candidate or treat the new
code as having passed the old candidate's observation window.
