# iCIMS plugin tutorial

`swe-scraper-icims` is an experimental 0.x plugin for public iCIMS portals.

## Install

```powershell
py -m pip install --pre swe-internship-scraper swe-scraper-icims
py -m swe_scraper providers
```

The main scraper works without this package. Install the plugin only if you
have a public iCIMS search URL; the current release is experimental.

The provider appears as `icims` through the `swe_scraper.providers` entry-point group.

## Configure

```json
{
  "icims": [
    {
      "name": "Example",
      "slug": "careers-example.icims.com",
      "search_url": "https://careers-example.icims.com/jobs/search",
      "max_pages": 10
    }
  ]
}
```

`search_url` must be HTTPS and use the same hostname as `slug`. The plugin follows only same-origin job links and explicit `rel="next"` pagination. It reads JSON-LD `JobPosting` first and known public markup second.

Save the example with your real portal URL as `icims-targets.json`, then run
`py -m swe_scraper scan --targets icims-targets.json --output icims-jobs.csv`.

The plugin stops on authentication, access challenges, malformed pages, repeated pages, and page limits. It does not submit applications or bypass access controls.
