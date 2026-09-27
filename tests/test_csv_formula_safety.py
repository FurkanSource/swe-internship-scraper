"""CSV presents ATS text to spreadsheet users without initial formula cells."""

import csv
import tempfile
import unittest
from pathlib import Path

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.exporters.csv_exporter import write_csv
from swe_scraper.models import Job, ScanResult


class CsvFormulaSafetyTests(unittest.TestCase):
    def test_formula_leaders_are_prefixed_without_changing_json_values(self):
        titles = ("=1+1", "+cmd", "-5", "@SUM(A1:A10)", " =1+1", "\t=1+1")
        jobs = [
            Job(
                id=f"fixture:{index}",
                company="=Example" if index == 0 else "Example",
                title=title,
                application_url=f"https://example.test/jobs/{index}",
                provider="fixture",
                source_job_id=str(index),
                locations=("New York, NY",),
            )
            for index, title in enumerate(titles)
        ]
        result = ScanResult.from_iterables(jobs)
        with tempfile.TemporaryDirectory() as directory:
            path = write_csv(result, Path(directory) / "jobs.csv")
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual([row["title"] for row in rows], ["'" + title for title in titles])
        self.assertEqual(rows[0]["company"], "'=Example")
        self.assertEqual(rows[0]["locations"], "New York, NY")
        self.assertEqual(rows[0]["posted_at"], "")
        self.assertEqual(rows[0]["description"], "")
        self.assertEqual(result.to_dict()["jobs"][0]["title"], "=1+1")


if __name__ == "__main__":
    unittest.main()
