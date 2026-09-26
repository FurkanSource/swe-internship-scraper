"""Ashby public posting API adapter."""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..models import Job
from ..normalize import iso_datetime, normalize_locations
from .base import JsonClient, Target

BOARD_QUERY = """
query($org: String!, $cursor: String) {
  jobBoard(organizationHostedJobsPageName: $org) {
    jobPostings(first: 100, after: $cursor) {
      nodes {
        id
        title
        locationName
        isRemote
        publishedAt
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

TEAMS_QUERY = """
query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
  jobBoard: jobBoardWithTeams(
    organizationHostedJobsPageName: $organizationHostedJobsPageName
  ) {
    jobPostings {
      id
      title
      locationName
      secondaryLocations { locationName }
    }
  }
}
"""


class AshbyProvider:
    name = "ashby"
    required_options: tuple[str, ...] = ()

    def validate_target(self, target: Target) -> None:
        if not target.slug.strip():
            raise ValueError("Ashby target requires a board slug")

    def fetch(self, target: Target, client: JsonClient) -> list[Job]:
        self.validate_target(target)
        slug = urllib.parse.quote(target.slug, safe="")
        try:
            payload = client.get_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            )
        except Exception as public_error:
            nodes = []
            cursor = None
            seen_cursors: set[str] = set()
            max_pages = min(max(int(target.options.get("max_pages", 5)), 1), 20)
            for _ in range(max_pages):
                graph = client.post_json(
                    "https://jobs.ashbyhq.com/api/non-user-graphql",
                    {
                        "query": BOARD_QUERY,
                        "variables": {"org": target.slug, "cursor": cursor},
                    },
                )
                errors = graph.get("errors") if isinstance(graph, dict) else None
                if errors:
                    if 'Cannot query field "jobBoard"' not in str(errors):
                        raise RuntimeError(
                            f"Ashby GraphQL errors for '{target.slug}': {errors}"
                        ) from public_error
                    graph = client.post_json(
                        "https://jobs.ashbyhq.com/api/non-user-graphql"
                        "?op=ApiJobBoardWithTeams",
                        {
                            "operationName": "ApiJobBoardWithTeams",
                            "variables": {"organizationHostedJobsPageName": target.slug},
                            "query": TEAMS_QUERY,
                        },
                    )
                    if not isinstance(graph, dict) or graph.get("errors"):
                        raise RuntimeError(
                            f"Ashby GraphQL errors for '{target.slug}': "
                            f"{graph.get('errors') if isinstance(graph, dict) else graph}"
                        ) from public_error
                board = (
                    (graph.get("data") or {}).get("jobBoard")
                    if isinstance(graph, dict)
                    else None
                )
                if not isinstance(board, dict):
                    raise RuntimeError(
                        f"Ashby board '{target.slug}' not found (jobBoard is null)"
                    ) from public_error
                postings = board.get("jobPostings")
                if isinstance(postings, list):
                    nodes.extend(postings)
                    break
                if not isinstance(postings, dict) or not isinstance(
                    postings.get("nodes"), list
                ):
                    raise ValueError(
                        "Ashby GraphQL response must contain a postings list"
                    ) from public_error
                nodes.extend(postings["nodes"])
                page_info = postings.get("pageInfo")
                if not isinstance(page_info, dict) or not isinstance(
                    page_info.get("hasNextPage"), bool
                ):
                    raise ValueError(
                        "Ashby GraphQL pagination requires boolean hasNextPage"
                    ) from public_error
                if not page_info["hasNextPage"]:
                    break
                next_cursor = str(page_info.get("endCursor") or "")
                if not next_cursor:
                    raise RuntimeError(
                        f"Ashby board '{target.slug}' pagination error: "
                        "hasNextPage is True but endCursor is missing"
                    ) from public_error
                if next_cursor in seen_cursors:
                    raise RuntimeError(
                        f"Ashby board '{target.slug}' pagination error: repeated "
                        f"cursor '{next_cursor}' detected"
                    ) from public_error
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                raise RuntimeError(
                    f"Ashby board '{target.slug}' reached max pagination limit "
                    f"({max_pages} pages) with more postings remaining; marked incomplete"
                ) from public_error
            payload = {"jobPostings": nodes}
        if not isinstance(payload, dict):
            raise ValueError("Ashby response must be a job-board object")
        rows = payload.get("jobs") if "jobs" in payload else payload.get("jobPostings")
        if isinstance(rows, dict):
            rows = rows.get("nodes")
        if not isinstance(rows, list):
            raise ValueError("Ashby response must contain a postings list")
        jobs = self.parse(target, {"jobs": rows})
        if len(jobs) != len(rows):
            raise ValueError("Ashby response contains malformed job records")
        return jobs

    def parse(self, target: Target, payload: Any) -> list[Job]:
        """Normalize either supported Ashby public response shape."""
        if not isinstance(payload, dict):
            return []
        rows = payload.get("jobs") or payload.get("jobPostings") or []
        if isinstance(rows, dict):
            rows = rows.get("nodes") or []
        jobs: list[Job] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("id") or row.get("jobPostingId") or "").strip()
            title = str(row.get("title") or "").strip()
            url = str(
                row.get("applyUrl")
                or row.get("jobUrl")
                or f"https://jobs.ashbyhq.com/{target.slug}/{source_id}"
            ).strip()
            if not source_id or not title:
                continue
            secondary = row.get("secondaryLocations") or []
            locations = [row.get("location") or row.get("locationName") or ""]
            locations.extend(
                value.get("locationName", "") if isinstance(value, dict) else value
                for value in secondary
            )
            location_values = normalize_locations(locations)
            remote = bool(row.get("isRemote")) or any(
                "remote" in value.casefold() for value in location_values
            )
            jobs.append(
                Job(
                    id=f"ashby:{target.slug}:{source_id}",
                    company=target.name,
                    title=title,
                    application_url=url,
                    provider=self.name,
                    source_job_id=source_id,
                    locations=location_values,
                    posted_at=iso_datetime(row.get("publishedAt")),
                    description=str(
                        row.get("descriptionPlain") or row.get("description") or ""
                    ),
                    remote=remote,
                    metadata={
                        "board": target.slug,
                        "source_updated_at_raw": str(row.get("publishedAt") or ""),
                    },
                )
            )
        return jobs
