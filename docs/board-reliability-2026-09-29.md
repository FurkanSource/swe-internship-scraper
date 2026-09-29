# Priority board reliability check — 2026-09-29

The previous 76-board priority scan returned 216 matches and nine board errors.
This check tested the affected public ATS endpoints directly before updating the
catalog. It does not claim that every board in the full catalog is healthy.

| Affected source | Finding | Action |
| --- | --- | --- |
| Anthropic Ashby | Public posting endpoint returned 404; its Greenhouse board returned jobs with direct URLs. | Remove stale Ashby duplicate; retain priority Greenhouse target. |
| Brex Ashby | Public posting endpoint returned 404; its Greenhouse board returned jobs with direct URLs. | Replace with priority Greenhouse target. |
| Applied Intuition Greenhouse | Board returned 404; the company's [current careers page](https://www.appliedintuition.com/careers) links to Ashby `applied`. | Remove stale Greenhouse duplicate; promote existing Ashby target to priority. |
| Talos Ashby `talos` | Public posting endpoint returned 404; [current Talos roles](https://www.talos.com/working/open-roles) link to `Talos-Trading`. | Remove stale duplicate; promote existing Ashby target to priority. |
| Retool and Superhuman Ashby | Both public posting endpoints returned 404. Their current careers pages did not establish a supported replacement ATS board. | Remove broken targets. [Retool](https://retool.com/careers) and [Superhuman](https://superhuman.com/company/careers) remain manual coverage gaps. |
| Anduril Greenhouse | The full-content response exceeded the HTTP client's 10 MiB bound. The compact listing returned 2,392 records and direct URLs. | Use compact listings for this target. Fetch details for candidate jobs when scan constraints need descriptions. Unconstrained scans leave descriptions empty for this board. |
| HPE Workday | Earlier full runs failed on rows without titles; this check's complete run succeeded with 12 matching jobs. | Retain with explicit observation; do not call the intermittent source fixed. |
| TD Workday | Full run failed again on a missing-title row, now at offset 860 despite the page retry. | Retire from the supported catalog. [TD careers](https://careers.td.com/) remain a manual coverage gap until its public pagination can be scanned completely. |

The repaired catalog contains 1,292 boards, including 72 priority boards.
The five repaired priority sources (Anduril, Anthropic, Brex, Applied Intuition,
and Talos) completed a live scan with no provider errors and eight matching roles.
The two Workday targets were checked separately: HPE returned 12 matching roles;
TD failed and returned no partial board results. A fresh full priority scan and
candidate health observation are separate release checks.

The first complete 72-board priority replay returned 199 matches and **two
provider errors** in 118 seconds. Anduril's compact Greenhouse listing timed out
under concurrent load, although it completed alone. NVIDIA's Workday board
returned a row without a title or job path at offset 1020; a direct recheck of
that page reproduced the empty row. The scanner reported both errors and did
not emit partial results from either board. These are unresolved reliability
risks for the release candidate and must be assessed by its daily and weekly
observations before stable promotion.
