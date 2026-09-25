# Releasing

The release workflow builds the scraper and experimental iCIMS plugin once, checks
their distributions, creates an SBOM and checksums, records build provenance, and
publishes to PyPI before creating a GitHub Release. The `pypi` GitHub environment
permits version tags.

## First release setup

Create two [pending PyPI trusted publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
from the PyPI account that will own the distributions:

| PyPI project | GitHub owner | Repository | Workflow | Environment |
| --- | --- | --- | --- | --- |
| `swe-internship-scraper` | `FurkanSource` | `swe-internship-scraper` | `release.yml` | `pypi` |
| `swe-scraper-icims` | `FurkanSource` | `swe-internship-scraper` | `release.yml` | `pypi` |

Pending publishers do not reserve project names until the first upload. Check both
names again immediately before publishing. The PyPI publishing job's OIDC identity
matches both projects, so PyPI can issue a short-lived token scoped to both.

## Release candidate

1. Confirm the public audit, CI, Security, 18 daily canaries, and 60-target weekly
   health report pass on the commit to release.
2. Set the `1.0.0rc1` changelog date to the release date and create a signed
   `v1.0.0rc1` tag on that verified commit.
3. Push the tag. Check the Release workflow's build, provenance, PyPI, and GitHub
   Release jobs, then install both distributions from PyPI in clean environments.
4. Start the soak window after the release candidate is published. Require seven
   consecutive healthy daily runs, one successful weekly sample, clean Windows and
   Linux installs, and no schema or provider regressions.

Promote to `1.0.0` with the planned version and changelog update after the soak
gate. Then pin the private tracker to the stable PyPI wheel and remove its embedded
package copy. If a release job fails after PyPI upload, inspect which distributions
were published before retrying; PyPI will not accept the same filename twice.
