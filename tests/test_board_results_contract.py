"""Board observations are the source of truth for public-list closure decisions."""

from __future__ import annotations

import pytest
from _bootstrap import ROOT  # noqa: F401

from swe_scraper.board_results import target_key
from swe_scraper.cli import main
from swe_scraper.models import Job
from swe_scraper.providers.base import Target
from swe_scraper.scanner import scan_targets


def _job(source_id: str) -> Job:
    return Job(
        id=f"oracle:{source_id}",
        company="Example",
        title="Software Engineer Intern Summer 2027",
        application_url=f"https://example.com/jobs/{source_id}",
        provider="oracle",
        source_job_id=source_id,
    )


def test_target_key_scopes_shared_oracle_slug() -> None:
    one = Target("oracle", "A", "cx_1", {"origin": "https://a.example", "site": "1"})
    renamed = Target(
        "oracle",
        "Renamed",
        "cx_1",
        {"origin": "https://a.example/", "site": "1", "max_pages": 2},
    )
    other = Target("oracle", "B", "cx_1", {"origin": "https://b.example", "site": "1"})
    assert target_key(one) == target_key(renamed)
    assert target_key(one) != target_key(other)


def test_board_results_include_complete_empty_failed_and_job_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [
        Target("oracle", "A", "cx_1", {"origin": "https://a.example", "site": "1"}),
        Target("oracle", "B", "cx_1", {"origin": "https://b.example", "site": "1"}),
        Target("oracle", "C", "cx_1", {"origin": "https://c.example", "site": "1"}),
    ]

    class Provider:
        def validate_target(self, target: Target) -> None:
            pass

        def fetch(self, target: Target, client: object) -> list[Job]:
            if target.name == "A":
                return [_job("123")]
            if target.name == "B":
                return []
            raise RuntimeError("board unavailable")

    monkeypatch.setattr("swe_scraper.scanner.get_provider", lambda _: Provider())
    result = scan_targets(
        targets, client=object(), include_board_results=True, max_workers=1
    ).to_dict()
    boards = result["board_results"]
    assert [board["status"] for board in boards] == ["complete", "complete", "failed"]
    assert [board["job_count"] for board in boards] == [1, 0, 0]
    assert len({board["target_key"] for board in boards}) == 3
    assert boards[0]["sources"] == result["jobs"][0]["sources"]
    assert boards[1]["sources"] == []
    assert result["schema_version"] == 2


def test_duplicate_target_key_rejected_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provider:
        def validate_target(self, target: Target) -> None:
            pass

        def fetch(self, target: Target, client: object) -> list[Job]:
            raise AssertionError("should not fetch")

    monkeypatch.setattr("swe_scraper.scanner.get_provider", lambda _: Provider())
    targets = [
        Target("oracle", "A", "cx_1", {"origin": "https://a.example"}),
        Target("oracle", "Renamed", "cx_1", {"origin": "https://a.example/"}),
    ]
    with pytest.raises(ValueError, match="duplicate target_key"):
        scan_targets(targets, client=object(), include_board_results=True)


def test_default_json_contract_omits_board_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class Provider:
        def validate_target(self, target: Target) -> None:
            pass

        def fetch(self, target: Target, client: object) -> list[Job]:
            return []

    monkeypatch.setattr("swe_scraper.scanner.get_provider", lambda _: Provider())
    result = scan_targets(
        [Target("oracle", "A", "cx_1", {"origin": "https://a.example"})],
        client=object(),
    )
    assert "board_results" not in result.to_dict()


def test_board_results_reject_csv_before_network(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "scan",
                "--quick",
                "--include-board-results",
                "--output",
                str(tmp_path / "jobs.csv"),
            ]
        )
    assert exc.value.code == 2
    assert not (tmp_path / "jobs.csv").exists()
