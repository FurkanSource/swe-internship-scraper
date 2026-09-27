"""Observable role, location, and keyword decisions for public scans."""

import json
import unittest

from _bootstrap import ROOT

from swe_scraper.filters import filter_jobs, is_swe_internship
from swe_scraper.models import Job


def job(title="Software Engineer Intern", *, location="Remote", description=""):
    return Job(
        id="fixture:1",
        company="Example",
        title=title,
        application_url="https://example.test/jobs/1",
        provider="fixture",
        source_job_id="1",
        locations=(location,),
        description=description,
    )


class FilterPrecisionTests(unittest.TestCase):
    def test_labeled_titles(self):
        fixture = json.loads(
            (ROOT / "tests/fixtures/filter_titles.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            sum(len(titles) for split in fixture.values() for titles in split.values()), 100
        )
        for split, groups in fixture.items():
            for title in groups["core"]:
                with self.subTest(split=split, title=title):
                    self.assertTrue(is_swe_internship(title))
            for category in ("adjacent", "reject"):
                for title in groups[category]:
                    with self.subTest(split=split, title=title):
                        self.assertFalse(is_swe_internship(title))

    def test_location_aliases_do_not_match_substrings(self):
        locations = (
            "New York, NY",
            "New York City",
            "Albany, NY",
            "San Francisco, CA",
            "Berlin, Germany",
            "Sunnyvale, CA",
            "Sydney, Australia",
        )
        ny = [
            value
            for value in locations
            if filter_jobs([job(location=value)], locations=["ny"])
        ]
        sf = [
            value
            for value in locations
            if filter_jobs([job(location=value)], locations=["sf"])
        ]
        self.assertEqual(ny, list(locations[:3]))
        self.assertEqual(sf, ["San Francisco, CA"])

    def test_language_terms_respect_punctuation(self):
        cases = (
            ("Go", "Build services in Go", True),
            ("go", "Improve the algorithm", False),
            ("C++", "Write C++ services", True),
            ("C++", "Write C+++ services", False),
            ("C#", "Write C# services", True),
            ("C#", "Write C## services", False),
            (".NET", "Build .NET services", True),
            (".NET", "Build .NETworks", False),
            ("Node.js", "Build Node.js services", True),
            ("Node.js", "Build Node.jsx services", False),
        )
        for term, description, expected in cases:
            with self.subTest(term=term, description=description):
                self.assertEqual(
                    bool(
                        filter_jobs([job(description=description)], include_keywords=[term])
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
