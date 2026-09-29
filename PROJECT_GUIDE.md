# Project guide

## License

The scraper and optional iCIMS plugin use the MIT license. Reuse is permitted
under the terms in [LICENSE](LICENSE); keep its copyright and license notice
with copies. The plugin includes the same notice in its own distribution.
The code license does not grant rights to third-party job content.

## Help and security

Use GitHub Discussions for questions and Issues for reproducible bugs. Include
the command, provider, sanitized target, Python version and traceback. Never
post credentials, cookies, resumes, applicant data or private portal URLs.

Report vulnerabilities through GitHub private vulnerability reporting when
available. Otherwise ask the maintainer for a private contact before sharing
exploit details. Include reproduction steps, impact and any mitigation.

Providers access public endpoints, with bounded requests and pagination. The
scraper does not authenticate, bypass access challenges or submit applications.
Public packages exclude tracker code, applicant data and notification secrets.

## Contributing

Preserve direct application URLs, report incomplete data as a failure and keep
personal eligibility rules outside the package. Explain changes with evidence,
update relevant docs and add sanitized provider fixtures for normal, malformed
and incomplete responses. Live checks belong in the scheduled health workflow.
See the [provider development guide](docs/provider-development.md).

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements-dev.lock -e .

ruff format --check src tests plugins scripts
ruff check src tests plugins scripts
py -m mypy
py -m coverage run -m unittest discover -s tests -p "test_*.py"
py -m coverage run --append -m unittest discover -s plugins/icims/tests -p "test_*.py"
py -m coverage report
py -m build
py -m twine check dist/*
```

## Conduct

Be respectful and constructive in issues, reviews, discussions and project
events. Harassment, discrimination, threats, publishing private information and
disruption are unacceptable. Report concerns privately using contact information
on the maintainer's GitHub profile. Maintainers may remove content or restrict
participation to protect contributors.

## Compatibility and maintenance

- **Python:** 1.x supports CPython 3.10–3.14. Tests run on Windows and Linux;
  clean installs run on Windows, Linux and macOS with Python 3.12.
- **API:** `Job`, `JobSource`, `MatchEvidence`, `ScanResult`, `Target`, `HttpClient`
  and provider entry points follow semantic versioning. The deprecated
  `JsonClient` alias remains available through 1.x.
- **Output:** writers emit schema v2; readers accept v1 and v2 throughout 1.x.
  Optional fields may be added in minor releases; changing or removing required
  fields needs a major release.
- **Fixes:** support targets the latest stable minor line and active release
  candidate. Security fixes target the latest stable release and active candidate.
  Release and upgrade checks follow the [release guide](docs/release-roadmap.md).
- **Providers:** core providers follow the [provider support policy](docs/provider-support.md).
  Experimental HTML plugins use independent 0.x versions; markup changes may
  require updates and they do not inherit core stability guarantees.
