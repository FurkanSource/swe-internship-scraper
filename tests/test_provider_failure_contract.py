import unittest

from _bootstrap import ROOT  # noqa: F401

from swe_scraper.models import ProviderFailure


class ProviderFailureContractTests(unittest.TestCase):
    def test_defaults_preserve_the_exact_legacy_payload(self):
        failure = ProviderFailure("oracle", "Example", "careers", "failed")
        self.assertFalse(failure.partial)
        self.assertEqual(failure.details, ())
        self.assertEqual(
            failure.to_dict(),
            {
                "provider": "oracle",
                "company": "Example",
                "slug": "careers",
                "error": "failed",
            },
        )

    def test_partial_and_record_details_are_optional_independent_fields(self):
        detail = {"stage": "record", "source_job_id": "1001", "error": "missing title"}
        for partial, details in ((True, ()), (False, (detail,)), (True, (detail,))):
            with self.subTest(partial=partial, details=details):
                failure = ProviderFailure(
                    "oracle",
                    "Example",
                    "careers",
                    "failed",
                    partial=partial,
                    details=details,
                )
                payload = failure.to_dict()
                self.assertEqual("partial" in payload, partial)
                self.assertEqual("details" in payload, bool(details))
                if partial:
                    self.assertIs(payload["partial"], True)
                if details:
                    self.assertEqual(payload["details"], [detail])


if __name__ == "__main__":
    unittest.main(verbosity=2)
