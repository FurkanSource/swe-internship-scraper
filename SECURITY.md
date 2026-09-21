# Security policy

## Supported versions

Security fixes are applied to the latest stable release and the active release candidate.

## Reporting

Use GitHub private vulnerability reporting. Include the affected module, reproduction, impact, and mitigation when available. Do not place API keys, cookies, applicant data, private portal URLs, or exploit details in a public issue.

## Boundaries

- Providers access configured public ATS endpoints.
- Requests have bounded retries, response size, rate, and pagination.
- Target origins validate before network access.
- The scraper does not authenticate, solve access challenges, or submit applications.
- The public distribution contains no applicant database, profile, tracker, or notification secret.
