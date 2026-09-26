"""Stable publishing must fail closed on missing or stale candidate evidence."""

import datetime as dt
import io
import json
import unittest
import zipfile
from unittest import mock

from _bootstrap import ROOT  # noqa: F401

from scripts.release_stability import check_metadata_only, check_remote, evaluate_reports

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 10, 12, tzinfo=UTC)
PUBLISHED = NOW - dt.timedelta(days=10)


def report(day, mode="daily", status="healthy"):
    providers = ("greenhouse", "lever", "ashby", "workday", "oracle", "smartrecruiters")
    return {
        "candidate_tag": "v1.0.0rc3",
        "candidate_sha": "a" * 40,
        "version": "1.0.0rc3",
        "mode": mode,
        "checked_at": (NOW - dt.timedelta(days=day)).isoformat(),
        "status": status,
        "exit_code": 0 if status == "healthy" else 2,
        "checks": [
            {
                "provider": provider,
                "target": str(i),
                "status": "healthy",
                "pagination_complete": True,
            }
            for provider in providers
            for i in range(3 if mode == "daily" else 10)
        ],
    }


class ReleaseStabilityTests(unittest.TestCase):
    def check(self, reports):
        return evaluate_reports(reports, "v1.0.0rc3", "a" * 40, NOW, PUBLISHED)

    def test_seven_distinct_days_and_weekly_sample_pass(self):
        self.assertEqual(
            self.check([report(i) for i in range(7)] + [report(2, "weekly")]), []
        )

    def test_duplicates_do_not_manufacture_seven_days(self):
        self.assertTrue(self.check([report(0)] * 7 + [report(0, "weekly")]))

    def test_missing_weekly_or_stale_streak_fails(self):
        self.assertTrue(self.check([report(i) for i in range(7)]))
        self.assertTrue(
            self.check([report(i + 3) for i in range(7)] + [report(3, "weekly")])
        )

    def test_failures_cannot_be_hidden_by_same_day_reruns(self):
        good = [report(i) for i in range(7)] + [report(2, "weekly")]
        self.assertTrue(self.check([*good, report(3, status="unhealthy")]))
        self.assertTrue(self.check([*good, report(0, "weekly", "unhealthy")]))

    def test_wrong_candidate_incomplete_and_prepublication_reports_fail(self):
        for mutation in (
            {"candidate_sha": "b" * 40},
            {"candidate_tag": "v1.0.0rc2"},
            {"version": "1.0.0rc2"},
            {"checks": []},
            {"checked_at": (PUBLISHED - dt.timedelta(days=1)).isoformat()},
        ):
            reports = [report(i) | mutation for i in range(7)] + [report(2, "weekly")]
            with self.subTest(mutation=mutation):
                self.assertTrue(self.check(reports))

    def test_partial_or_duplicate_targets_fail(self):
        for bad_check in (
            {"status": "unhealthy"},
            {"pagination_complete": False},
            {"target": "1"},
        ):
            reports = [report(i) for i in range(7)] + [report(2, "weekly")]
            reports[0]["checks"][0].update(bad_check)
            self.assertTrue(self.check(reports))

    def test_yesterday_is_allowed_but_future_observations_are_not(self):
        self.assertEqual(
            self.check([report(i + 1) for i in range(7)] + [report(2, "weekly")]), []
        )
        self.assertTrue(self.check([report(-i) for i in range(7)] + [report(0, "weekly")]))

    def test_promotion_allows_only_exact_version_changes_and_changelog(self):
        old = {
            "pyproject.toml": 'version = "1.0.0rc3"\n',
            "src/swe_scraper/__init__.py": '__version__ = "1.0.0rc3"\n',
            "CHANGELOG.md": "old",
        }
        new = {p: text.replace("1.0.0rc3", "1.0.0") for p, text in old.items()}
        new["CHANGELOG.md"] = "new"
        check_metadata_only(old, new, "1.0.0rc3", "1.0.0")
        for path in ("src/swe_scraper/filters.py", "pyproject.toml"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                check_metadata_only(
                    old, new | {path: "changed behavior"}, "1.0.0rc3", "1.0.0"
                )

    def test_remote_gate_requires_release_success_and_unexpired_run_bound_artifacts(self):
        def run_gate(*, release_ok=True, expired=False, mismatch=False, failed=False):
            reports = [report(i) for i in range(7)] + [report(2, "weekly")]
            runs = [
                {
                    "id": i,
                    "run_attempt": 1,
                    "created_at": item["checked_at"],
                    "status": "completed",
                    "conclusion": "failure" if failed else "success",
                }
                for i, item in enumerate(reports)
            ]

            def pages(path, key):
                if "release.yml" in path:
                    return [
                        {
                            "head_branch": "v1.0.0rc3",
                            "conclusion": "success" if release_ok else "failure",
                        }
                    ]
                if key == "workflow_runs":
                    return runs
                run_id = int(path.split("/runs/")[1].split("/")[0])
                return [{"id": run_id, "name": f"candidate-{run_id}-1", "expired": expired}]

            def command(*args):
                if args[:2] == ("git", "rev-parse"):
                    return b"a" * 40
                if args[:2] == ("git", "diff"):
                    return b""
                index = int(args[2].split("/artifacts/")[1].split("/")[0])
                data = reports[index] | {
                    "run_id": index + (1 if mismatch else 0),
                    "run_attempt": 1,
                }
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr("stability.json", json.dumps(data))
                return buffer.getvalue()

            fromisoformat = dt.datetime.fromisoformat
            with (
                mock.patch("scripts.release_stability.pages", side_effect=pages),
                mock.patch("scripts.release_stability.command", side_effect=command),
                mock.patch(
                    "scripts.release_stability.api",
                    return_value={
                        "draft": False,
                        "prerelease": True,
                        "published_at": PUBLISHED.isoformat(),
                    },
                ),
                mock.patch("scripts.release_stability.dt.datetime") as clock,
            ):
                clock.now.return_value = NOW
                clock.fromisoformat.side_effect = fromisoformat
                check_remote(
                    "owner/repo", {"tag": "v1.0.0rc3", "version": "1.0.0rc3"}, "1.0.0"
                )

        run_gate()
        for options in (
            {"release_ok": False},
            {"expired": True},
            {"mismatch": True},
            {"failed": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                run_gate(**options)
