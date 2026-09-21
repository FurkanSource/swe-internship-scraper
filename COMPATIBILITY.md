# Compatibility policy

## Python

The 1.x line supports CPython 3.10 through 3.14. CI runs package tests and clean-install smoke tests on Windows and Linux.

## Public API

The `Job`, `JobSource`, `MatchEvidence`, `ScanResult`, `Target`, `HttpClient`, and provider entry-point contracts follow semantic versioning. `JsonClient` is a deprecated alias and remains available through 1.x.

## Data schema

Writers emit schema v2. Readers accept schemas v1 and v2 throughout 1.x. New optional fields may be added in minor versions. Removing or changing required fields requires a major release.

## Providers

Core providers follow the supported policy in [docs/provider-support.md](docs/provider-support.md). Experimental HTML plugins version independently and do not inherit the core stability guarantee.
