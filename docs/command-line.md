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
Output ending in `.csv` is a spreadsheet; output ending in `.json` contains the
full schema, including source and merge information. Files are written to the
current folder unless you provide another path.

Filter results with repeatable options:

```powershell
py -m swe_scraper scan --location "New York" --include python --output nyc-python.csv
py -m swe_scraper scan --providers greenhouse,oracle --output selected.csv
```

Filters apply to job results; they do not reduce the number of boards fetched.
If some boards fail, the command reports their error count. JSON output also
retains provider failures; CSV contains job rows only.

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

## Reports and repeated scans

```powershell
py -m swe_scraper scan --dedupe-report possible-duplicates.json --output jobs.json
py -m swe_scraper validate jobs.json
py -m swe_scraper watch --once --output jobs-latest.json
py -m swe_scraper health --output provider-health.json
```

`watch` saves a local seen-jobs file so later runs can report new roles. Without
`--once`, it repeats every six hours by default; press Ctrl+C to stop it.
`health` checks the monitored boards and exits `0` when all pass, `1` when a
provider still meets quorum despite a failed board, and `2` for a provider
quorum, contract, or pagination failure.

For the optional iCIMS provider, see the [plugin guide](icims-plugin.md).
