"""Incomplete board results carried without weakening the strict fetch contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import Job


@dataclass(frozen=True)
class FetchIssue:
    stage: str
    source_id: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BoardFetchResult:
    jobs: tuple[Job, ...]
    issues: tuple[FetchIssue, ...]
    pagination_complete: bool


class PartialFetchError(RuntimeError):
    """Strict fetch still raises; an explicit recovery scan may retain these jobs."""

    def __init__(self, result: BoardFetchResult) -> None:
        self.result = result
        detail = "; ".join(issue.error for issue in result.issues[:3])
        super().__init__(f"incomplete board ({len(result.issues)} issue(s)): {detail}")
