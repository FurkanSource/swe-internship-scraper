# Provider development guide

## Contract

```python
class Provider:
    name = "provider-name"
    required_options = ("origin",)

    def validate_target(self, target: Target) -> None:
        ...

    def fetch(self, target: Target, client: HttpClient) -> list[Job]:
        ...
```

Validation must reject missing, unsafe, or unbounded options before network access. `HttpClient` supports bounded `get_json`, `post_json`, and `get_text`. `JsonClient` is a deprecated compatibility alias through 1.x.

Third-party packages register a provider class or instance:

```toml
[project.entry-points."swe_scraper.providers"]
example = "example_provider:ExampleProvider"
```

## Support requirements

1. Use an official public employer or ATS endpoint.
2. Preserve provider identity and a direct application URL.
3. Return all valid postings; shared SWE filtering runs later.
4. Keep unknown dates empty.
5. Bound response size, retries, pages, and repeated-page detection.
6. Raise on malformed or known-incomplete responses.
7. Use sanitized offline fixtures for request, parse, and pagination contracts.
8. Add three live canaries before declaring a provider supported.

HTML providers remain experimental plugins. They must stay on the configured origin and must not bypass authentication, CAPTCHA, or access controls. See the [iCIMS plugin tutorial](icims-plugin.md).

### Workday pagination

Workday uses the first page's total to verify completeness. Some CXS boards return
`total: 0` on later pages; this is not an end-of-results signal. Missing records,
repeated jobs, and changed nonzero totals fail the board instead of returning a
partial success. By default, requests use 20 postings per page and at most 20 pages
(400 postings). A target can set smaller `page_size` or `max_pages` limits;
exhausting a limit before the original total is reached is an error.

`search_text` defaults to `"intern"`; set it to `""` to search without that term.
Surrounding site slashes are normalized, and embedded path separators are rejected.

### SmartRecruiters and Oracle details

Both adapters fetch details with four workers by default. Set `detail_workers`
from 1 through 8 to adjust concurrency; use 1 with a custom HTTP client that is
not thread safe. The built-in client's per-host request spacing still applies.
Output order follows the listing order, regardless of detail completion order.

If a listing total changes during pagination, the adapter discards that attempt
and restarts listing once. Each attempt retains its configured page ceiling, so
at most twice that many list requests are made. A second total change, malformed
detail, repeated page, or incomplete response still fails the board. This avoids
reporting a partial board as complete.
