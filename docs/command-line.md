# Command line guide

Run the installed command as `swe-scraper` or, on Windows, as
`py -m swe_scraper`. Examples below use the Windows form. On macOS/Linux, use
the Python executable from your environment, such as `.venv/bin/python -m swe_scraper`.

## Scan jobs

```powershell
py -m swe_scraper scan --quick --output internships.csv
py -m swe_scraper scan --output internships.csv
py -m swe_scraper scan --target-set all --output all-internships.csv
```

`--quick` uses the small monitored sample. The default scans priority boards.
`--target-set all` scans the whole bundled catalog and can take much longer.
Full-catalog scans show periodic progress on stderr and checkpoint each completed
board in your operating system's user cache. If interrupted, rerun the same
command with `--resume` within 24 hours to reuse successful boards and retry
failed ones. A fresh run without `--resume` replaces the previous checkpoint
for that output path. Checkpoints are removed after all boards and the output
export succeed; they remain after a partial or failed run. Use `--no-progress`
to hide the status messages. `--resume` is available only with `--target-set all`.
Ctrl+C stops new boards and new requests between active operations, then retains
completed-board checkpoints and exits with code 130. Active socket operations
may take their configured timeout to finish; custom plugins must cooperate with
cancellation to stop promptly.

Use `--board-timeout SECONDS` for an optional total budget per board. Built-in
HTTP calls and retry/rate-limit waits observe this budget; it is not a hard
process kill and cannot forcibly interrupt arbitrary plugin code.

By default, a failed board contributes no jobs. `--allow-partial` instead retains
verified records with explicit failure metadata and a nonzero exit status. For
Workday listing errors it keeps the validated prefix (including valid rows from
a malformed page) and stops at that page. Oracle and SmartRecruiters can fetch
details for the final listing attempt's verified prefix; individual failed details
are isolated. Single-response Greenhouse, Lever and Ashby lists isolate malformed
rows. Unsupported plugins keep their strict behavior. Incomplete boards are never
saved as successful checkpoints, so resume retries them. Health and release gates
still require complete boards.

```powershell
py -m swe_scraper scan --target-set all --allow-partial --board-timeout 300 --output jobs.json
```

With `--allow-partial`, errors produce exit code 1 when some jobs remain, or 2
when none remain. JSON contains per-board errors and marks retained records with
`metadata.board_complete: false`. CSV retains its existing columns, so inspect
stderr and the exit code or use JSON when you need the failure details.

Output ending in `.csv` is a spreadsheet; output ending in `.json` contains the
full schema, including source and merge information. Files are written to the
current folder unless you provide another path.
CSV prefixes cells that could be read as formulas (`=`, `+`, `-`, `@`, including
after leading whitespace) with an apostrophe. This changes those cell values
and reduces formula interpretation when first opened in common spreadsheets;
it is not a guarantee after editing, saving, and reopening. For exact values
from the scraper's normalized output model, use JSON (`--format json`). Neither
format is a byte-for-byte copy of the ATS HTTP response.

Filter results with repeatable options:

```powershell
py -m swe_scraper scan --location "New York" --include python --output nyc-python.csv
py -m swe_scraper scan --providers greenhouse,oracle --output selected.csv
```

Filters apply to job results; they do not reduce the number of boards fetched.
Location and keyword options match complete terms rather than substrings.
`--location ny` matches `NY` and `New York`, while `--location sf` matches
`SF` and `San Francisco`. Symbols in terms such as `C++`, `C#`, `.NET`, and
`Node.js` are matched literally.
For SWE and adjacent scans, SmartRecruiters and Oracle skip detail requests for
list titles that clearly describe non-intern roles. Missing and ambiguous titles
are still fetched. `--all-jobs` continues to fetch every posting.
The default matches software internship titles, including SWE, backend, firmware,
and data engineering. Title matching is a heuristic; review the actual posting.
Use `--include-adjacent` to also consider analytics, quantitative research, and
other technical internships. Use `--all-jobs` to disable the software-internship
requirement. Explicit `--location`, `--include`, and `--exclude` filters still apply.
These two options are mutually exclusive.

### Workday scope and descriptions

Without an explicit target `search_text`, Workday fetches all listing pages;
the previous default was an upstream `intern` query. This also retains co-op
titles for local matching. A target's explicit `search_text`, including an empty
string, is preserved in every mode. `--all-jobs` disables local role filtering;
it does not override a target's explicitly restricted upstream search.

Workday uses at most 20 rows per page, honors `max_pages` (default 200, maximum
1000), and fails on short, repeated, malformed, or inconsistent pages. A larger
ceiling can take longer. Missing titles or direct paths remain failures; the
scraper does not discard unaccounted-for rows to declare a board complete.
Malformed rows on an otherwise structured page trigger one fresh recheck of that
same page. Persistent invalid rows and changed totals still fail.

Ordinary Workday scans use listing titles and locations without requesting every
description. Location or keyword filters fetch details for plausible internship
candidates, including additional locations. In `--all-jobs` mode these filters
require details for every listing. Workday descriptions are empty on the listing
path, with `metadata.details_complete=false`; requisition IDs are retained in
`metadata.summary_fields`, not used as job duties.

### Optional reuse of recent details

```powershell
py -m swe_scraper scan --cache-ttl 900 --output internships.csv
py -m swe_scraper scan --cache-ttl 900 --refresh --output internships.csv
```

Caching is disabled by default. `--cache-ttl` accepts 0–3600 seconds and is also
available for `watch`. Workday, SmartRecruiters and Oracle reuse validated public
detail responses only after fetching fresh, complete listings in the default mode.
Opt-in partial recovery can also reuse details for verified rows from incomplete
listings while retaining the board's failure. New or changed listing rows fetch
new details; deleted listings stay absent. `--refresh` bypasses reuse and updates
the cache. It requires a fresh scan rather than `--resume`.

Description-only changes with unchanged listings can lag until TTL expiry.
Health checks always make fresh requests. Cache hits retain the original
`metadata.detail_fetched_at`; `metadata.detail_cached` and
`metadata.detail_cache_max_age_seconds` identify reuse. Failed listings or expired
details produce failures, without a stale fallback. The cache is separate from
resume checkpoints and lives in the OS user cache under `swe-scraper/details`,
bounded to 4096 completed entries and 128 MiB (10 MiB per entry). Aggregate bounds
are best effort across processes or disk failures. Caching helps repeat scans;
it does not remove the listing cost of a first full-catalog scan.

If some boards fail, the command reports their error count. JSON output also
retains provider failures; CSV contains job rows only.

For automation that requires a complete scan:

```powershell
py -m swe_scraper scan --strict --output jobs.json
```

| Scan / watch exit code | Meaning |
| --- | --- |
| `0` | No provider errors, or partial results with jobs in the default mode |
| `1` | `--strict` or `--allow-partial`: some jobs were saved, but one or more providers failed |
| `2` | Provider errors with no matching jobs, or an invalid command/configuration |
| `130` | Interrupted by the user |

Results are saved before returning the provider-error status. A healthy scan with
zero matching jobs exits `0`.

## Custom boards

Create a JSON file like [the example](../examples/targets.example.json), then
run:

```powershell
py -m swe_scraper scan --targets my-targets.json --output jobs.csv
py -m swe_scraper providers --json
```

The example file uses placeholder company IDs, so replace them with real public
boards before scanning. Provider inventory shows each provider and its required
target options. Invalid options fail before the scraper makes a request.

Custom targets without `profiles` belong to `priority` and `all`. To include a
custom board in health checks or `--target-set canary`, explicitly give it
`"profiles": ["canary"]` (plus any other desired profiles). `--quick` uses only
the bundled canaries and cannot be combined with `--targets`.

## Reports and repeated scans

```powershell
py -m swe_scraper scan --dedupe-report possible-duplicates.json --output jobs.json
py -m swe_scraper validate jobs.json
py -m swe_scraper watch --once --output jobs-latest.json
py -m swe_scraper health --output provider-health.json
```

`watch` saves a local seen-jobs file so later runs can report new roles. Without
`--once`, it repeats every six hours by default; press Ctrl+C to stop it.
`watch --strict` stops at the first iteration with provider errors, using the
same exit codes as `scan --strict`, after saving available results and watch state.
`health` checks the monitored boards and exits `0` when all pass, `1` when a
provider still meets quorum despite a failed board, and `2` for a provider
quorum, contract, or pagination failure.

A valid, complete board with zero open jobs is healthy. Set a target's `min_jobs`
to a nonnegative integer to enforce an explicit minimum during health checks.
Malformed responses still fail, even if they contain no usable jobs.

On consoles that cannot display a job's characters, console output uses escaped
characters. JSON files and saved watch state retain the original text.

For the optional iCIMS provider, see the [plugin guide](icims-plugin.md).
