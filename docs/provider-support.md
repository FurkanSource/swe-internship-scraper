# Provider support policy

## Core providers

Greenhouse, Lever, Ashby, Workday, SmartRecruiters, and Oracle Recruiting are supported core providers.

A core provider must have:

- a documented public endpoint;
- pre-network target validation;
- bounded transport and fail-closed pagination;
- sanitized contract fixtures;
- three daily canaries;
- clear provider-level failures and direct application URLs.

Contract regressions that can return incomplete data are release blocking.

A successful, complete response with no open jobs is healthy. Targets may specify
`min_jobs` to require a minimum count in health checks; falling below it is
unhealthy. Malformed payloads and pagination failures remain contract failures,
not healthy empty boards. Provider quorum still requires two passing canaries.

## Experimental providers

HTML integrations ship as separate 0.x plugins. They must follow same-origin links, respect access controls, and prefer structured data. Markup changes may require plugin updates without a core release.

## Catalog entries

Catalog targets are added only after a live public response and a direct application URL are verified. The daily workflow checks 18 fixed canaries. The weekly workflow rotates deterministic samples across the full catalog.
