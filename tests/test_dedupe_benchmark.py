import json
import unittest

from _bootstrap import ROOT

from swe_scraper.dedupe import deduplicate
from swe_scraper.models import Job


class DeduplicationBenchmarkTests(unittest.TestCase):
    def test_release_quality_gate(self):
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "dedupe_benchmark.json").read_text(
                encoding="utf-8"
            )
        )
        pairs = fixture["pairs"]
        positives = [pair for pair in pairs if pair["label"] == "duplicate"]
        negatives = [pair for pair in pairs if pair["label"] == "distinct"]
        self.assertGreaterEqual(len(positives), 100)
        self.assertGreaterEqual(len(negatives), 250)

        true_merges = sum(
            len(
                deduplicate(
                    [Job.from_mapping(pair["left"]), Job.from_mapping(pair["right"])]
                )
            )
            == 1
            for pair in positives
        )
        false_merges = sum(
            len(
                deduplicate(
                    [Job.from_mapping(pair["left"]), Job.from_mapping(pair["right"])]
                )
            )
            == 1
            for pair in negatives
        )
        recall = true_merges / len(positives)
        self.assertEqual(false_merges, 0)
        self.assertGreaterEqual(recall, 0.95)


if __name__ == "__main__":
    unittest.main(verbosity=2)
