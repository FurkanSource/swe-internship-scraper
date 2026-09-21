import json
import unittest

from _bootstrap import ROOT

from swe_scraper.providers.ashby import AshbyProvider
from swe_scraper.providers.base import Target
from swe_scraper.providers.greenhouse import GreenhouseProvider
from swe_scraper.providers.lever import LeverProvider
from swe_scraper.providers.oracle import OracleProvider
from swe_scraper.providers.smartrecruiters import SmartRecruitersProvider
from swe_scraper.providers.workday import WorkdayProvider

FIXTURES = ROOT / "tests" / "fixtures" / "providers"


class ProviderContractFixtureTests(unittest.TestCase):
    def _fixture(self, name):
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    def test_recorded_greenhouse_contract(self):
        jobs = GreenhouseProvider().parse(
            Target("greenhouse", "Example", "example"),
            self._fixture("greenhouse"),
        )
        self.assertEqual(jobs[0].source_job_id, "1001")

    def test_recorded_lever_contract(self):
        jobs = LeverProvider().parse(
            Target("lever", "Example", "example"),
            self._fixture("lever"),
        )
        self.assertEqual(jobs[0].source_job_id, "lever-1001")

    def test_recorded_ashby_contract(self):
        jobs = AshbyProvider().parse(
            Target("ashby", "Example", "example"),
            self._fixture("ashby"),
        )
        self.assertEqual(jobs[0].source_job_id, "ashby-1001")

    def test_recorded_workday_page_contract(self):
        target = Target(
            "workday",
            "Example",
            "careers",
            {
                "origin": "https://example.wd1.myworkdayjobs.com",
                "tenant": "example",
                "site": "careers",
            },
        )
        jobs = WorkdayProvider().parse_page(target, self._fixture("workday"))
        self.assertEqual(jobs[0].source_job_id, "R-1001")

    def test_recorded_smartrecruiters_detail_contract(self):
        job = SmartRecruitersProvider().parse_detail(
            Target("smartrecruiters", "Example", "Example"),
            self._fixture("smartrecruiters-detail"),
        )
        self.assertIsNotNone(job)
        self.assertEqual(job.source_job_id, "sr-1001")

    def test_recorded_oracle_detail_contract(self):
        target = Target(
            "oracle",
            "Example",
            "CX_1",
            {"origin": "https://example.fa.us2.oraclecloud.com"},
        )
        job = OracleProvider().parse_detail(
            target,
            self._fixture("oracle-list")["items"][0]["requisitionList"]["items"][0],
            self._fixture("oracle-detail")["items"][0],
        )
        self.assertEqual(job.source_job_id, "1001")
        self.assertIn("/sites/CX_1/job/1001", job.application_url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
