import unittest

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.dedupe import deduplicate
from swe_scraper.filters import filter_jobs, is_swe_internship
from swe_scraper.models import SCHEMA_VERSION, Job, ScanResult
from swe_scraper.normalize import canonical_url, iso_datetime, normalize_locations


def job(**overrides):
    values = {
        "id": "greenhouse:acme:1",
        "company": "Acme",
        "title": "Software Engineering Intern",
        "application_url": "https://boards.greenhouse.io/acme/jobs/1?utm_source=test",
        "provider": "greenhouse",
        "source_job_id": "1",
        "locations": ("New York, NY",),
    }
    values.update(overrides)
    return Job(**values)


class ModelsAndNormalizationTests(unittest.TestCase):
    def test_job_round_trip_and_scan_schema(self):
        original = job()
        restored = Job.from_mapping(original.to_dict())
        self.assertEqual(restored, original)
        payload = ScanResult.from_iterables([original]).to_dict()
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["job_count"], 1)

    def test_job_rejects_missing_identity_and_generic_paths(self):
        with self.assertRaises(ValueError):
            job(application_url="")
        with self.assertRaises(ValueError):
            job(application_url="file:///tmp/job")

    def test_canonical_url_strips_tracking_and_normalizes_greenhouse(self):
        value = canonical_url(
            "https://boards.greenhouse.io/Acme/jobs/123/?utm_source=x&gh_jid=123#apply"
        )
        self.assertEqual(value, "https://job-boards.greenhouse.io/Acme/jobs/123")

    def test_normalizers_do_not_invent_dates_or_duplicate_locations(self):
        self.assertEqual(iso_datetime("not a date"), "")
        self.assertEqual(
            normalize_locations([" Remote ", "remote", "New York,   NY"]),
            ("Remote", "New York, NY"),
        )

    def test_public_filter_has_no_graduation_or_personal_location_assumption(self):
        self.assertTrue(is_swe_internship("Software Engineer Intern"))
        self.assertFalse(is_swe_internship("Senior Software Engineer"))
        self.assertFalse(is_swe_internship("Mechanical Engineering Intern"))
        jobs = [
            job(),
            job(id="2", source_job_id="2", title="Marketing Intern"),
            job(
                id="3",
                source_job_id="3",
                locations=("Austin, TX",),
                description="Collaborate with our New York office.",
            ),
        ]
        self.assertEqual(len(filter_jobs(jobs)), 2)
        self.assertEqual(len(filter_jobs(jobs, locations=["Austin"])), 1)
        self.assertEqual(len(filter_jobs(jobs, locations=["New York"])), 1)

    def test_dedupe_merges_exact_url_identity(self):
        duplicate = job(
            id="feed:1",
            provider="feed",
            source_job_id="feed-1",
            application_url="https://job-boards.greenhouse.io/acme/jobs/1",
            locations=("Remote",),
            description="Full description",
        )
        result = deduplicate([job(), duplicate])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].locations, ("New York, NY", "Remote"))
        self.assertEqual(result[0].description, "Full description")


if __name__ == "__main__":
    unittest.main(verbosity=2)
