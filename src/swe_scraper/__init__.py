"""Reusable software engineering internship scraper."""

__version__ = "1.0.0rc1"

from .models import Job, JobSource, MatchEvidence, ProviderFailure, ScanResult

__all__ = [
    "Job",
    "JobSource",
    "MatchEvidence",
    "ProviderFailure",
    "ScanResult",
]
