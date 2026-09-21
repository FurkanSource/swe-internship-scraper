import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(PLUGIN_ROOT / "src"))
sys.path.insert(0, str(CORE_SRC))

from swe_scraper_icims import IcimsProvider  # noqa: E402

from swe_scraper.providers.base import Target  # noqa: E402


class IcimsProviderTests(unittest.TestCase):
    def test_discovers_same_origin_jobs_and_explicit_next_page(self):
        html = """
        <html><head><link rel="next" href="/jobs/search?pr=1"></head><body>
          <a href="/jobs/123/software-engineer-intern/job">Role</a>
          <a href="https://evil.example/jobs/999/job">External</a>
        </body></html>
        """
        links, next_url = IcimsProvider().discover_page(
            html, "https://careers-acme.icims.com/jobs/search"
        )
        self.assertEqual(
            links,
            ("https://careers-acme.icims.com/jobs/123/software-engineer-intern/job",),
        )
        self.assertEqual(next_url, "https://careers-acme.icims.com/jobs/search?pr=1")

    def test_parses_json_ld_job_posting_before_dom_fallback(self):
        html = """
        <script type="application/ld+json">
        {
          "@type": "JobPosting",
          "title": "Software Engineering Intern",
          "description": "<p>Build reliable services.</p>",
          "datePosted": "2026-09-01",
          "jobLocation": {
            "address": {"addressLocality": "New York", "addressRegion": "NY"}
          },
          "hiringOrganization": {"name": "Acme"},
          "identifier": {"value": "123"}
        }
        </script>
        """
        target = Target(
            "icims",
            "Acme",
            "careers-acme.icims.com",
            {"search_url": "https://careers-acme.icims.com/jobs/search"},
        )
        parsed = IcimsProvider().parse_job_page(
            html,
            "https://careers-acme.icims.com/jobs/123/software-engineer-intern/job",
            target,
        )
        self.assertEqual(parsed.title, "Software Engineering Intern")
        self.assertEqual(parsed.source_job_id, "123")
        self.assertEqual(parsed.locations, ("New York, NY",))
        self.assertIn("Build reliable services", parsed.description)

    def test_rejects_cross_origin_search_configuration(self):
        target = Target(
            "icims",
            "Acme",
            "careers-acme.icims.com",
            {"search_url": "https://other.example/jobs/search"},
        )
        with self.assertRaisesRegex(ValueError, "same origin"):
            IcimsProvider().validate_target(target)

    def test_dom_fallback_and_nested_json_ld(self):
        target = Target(
            "icims",
            "Acme",
            "careers-acme.icims.com",
            {"search_url": "https://careers-acme.icims.com/jobs/search"},
        )
        fallback = """
        <h1>Platform Intern</h1>
        <div itemprop="jobLocation">Remote - US</div>
        <div itemprop="description"><p>Build platforms.</p></div>
        """
        job = IcimsProvider().parse_job_page(
            fallback,
            "https://careers-acme.icims.com/jobs/44/platform-intern/job",
            target,
        )
        self.assertEqual(job.source_job_id, "44")
        self.assertTrue(job.remote)
        self.assertIn("Build platforms", job.description)

        nested = """
        <script type="application/ld+json">not-json</script>
        <script type="application/ld+json">
        {"@graph": [{"@type": "WebPage"}, {
          "@type": "JobPosting", "title": "Security Intern",
          "description": "Secure systems", "identifier": "45",
          "jobLocation": [{"address": {
            "addressLocality": "Austin", "addressRegion": "TX",
            "addressCountry": "US"
          }}]
        }]}
        </script>
        """
        job = IcimsProvider().parse_job_page(
            nested,
            "https://careers-acme.icims.com/jobs/45/security-intern/job",
            target,
        )
        self.assertEqual(job.locations, ("Austin, TX, US",))

    def test_fetch_follows_explicit_pages_and_rejects_incomplete_pagination(self):
        search = "https://careers-acme.icims.com/jobs/search"
        page_two = f"{search}?pr=1"
        job_one = "https://careers-acme.icims.com/jobs/1/one/job"
        job_two = "https://careers-acme.icims.com/jobs/2/two/job"
        pages = {
            search: f'<a href="{job_one}">One</a><a rel="next" href="{page_two}">Next</a>',
            page_two: f'<a href="{job_two}">Two</a>',
            job_one: (
                '<script type="application/ld+json">'
                '{"@type":"JobPosting","title":"Intern One","identifier":"1"}'
                "</script>"
            ),
            job_two: (
                '<script type="application/ld+json">'
                '{"@type":"JobPosting","title":"Intern Two","identifier":"2"}'
                "</script>"
            ),
        }

        class Client:
            def get_text(self, url, **kwargs):
                return pages[url]

        target = Target(
            "icims",
            "Acme",
            "careers-acme.icims.com",
            {"search_url": search},
        )
        jobs = IcimsProvider().fetch(target, Client())
        self.assertEqual([job.source_job_id for job in jobs], ["1", "2"])

        limited = Target(
            "icims",
            "Acme",
            "careers-acme.icims.com",
            {"search_url": search, "max_pages": 1},
        )
        with self.assertRaisesRegex(RuntimeError, "max_pages"):
            IcimsProvider().fetch(limited, Client())

    def test_validation_and_malformed_pages_fail_closed(self):
        provider = IcimsProvider()
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            provider.validate_target(
                Target("icims", "Acme", "host", {"search_url": "http://host/jobs"})
            )
        with self.assertRaisesRegex(ValueError, "max_pages"):
            provider.fetch(
                Target(
                    "icims",
                    "Acme",
                    "host.icims.com",
                    {"search_url": "https://host.icims.com/jobs", "max_pages": 0},
                ),
                object(),
            )
        target = Target(
            "icims",
            "Acme",
            "host.icims.com",
            {"search_url": "https://host.icims.com/jobs"},
        )
        with self.assertRaisesRegex(ValueError, "missing title"):
            provider.parse_job_page(
                "<html></html>", "https://host.icims.com/jobs/1/missing/job", target
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
