import unittest

from _bootstrap import ROOT  # noqa: F401

from scripts.refresh_catalog import discover_candidates


class CatalogDiscoveryTests(unittest.TestCase):
    def test_extracts_smartrecruiters_and_oracle_targets(self):
        discovered = discover_candidates(
            [
                {
                    "company_name": "Example SR",
                    "url": "https://jobs.smartrecruiters.com/ExampleCo/123/role",
                },
                {
                    "company_name": "Example Oracle",
                    "url": (
                        "https://example.fa.us2.oraclecloud.com/hcmUI/"
                        "CandidateExperience/en/sites/CX_1/job/456"
                    ),
                },
            ]
        )
        self.assertEqual(discovered["smartrecruiters"][0]["slug"], "exampleco")
        self.assertEqual(discovered["oracle"][0]["slug"], "cx_1")
        self.assertEqual(
            discovered["oracle"][0]["origin"],
            "https://example.fa.us2.oraclecloud.com",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
