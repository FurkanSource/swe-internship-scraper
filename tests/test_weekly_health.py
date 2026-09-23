"""The weekly sample should rotate by week without catalog edit churn."""

import unittest

from _bootstrap import ROOT  # noqa: F401

from scripts.weekly_health import CORE_PROVIDERS, select_targets


class WeeklyHealthSelectionTests(unittest.TestCase):
    def test_selection_is_order_independent_and_stable_after_removal(self):
        catalog = {
            provider: [
                {"name": f"Target {index}", "slug": f"{provider}-{index}"}
                for index in range(15)
            ]
            for provider in CORE_PROVIDERS
        }
        original = select_targets(catalog, per_provider=10, seed="2026-W39")
        reversed_catalog = {key: list(reversed(rows)) for key, rows in catalog.items()}
        self.assertEqual(
            original,
            select_targets(reversed_catalog, per_provider=10, seed="2026-W39"),
        )

        removed_slug = next(
            target.slug for target in original if target.provider == "greenhouse"
        )
        catalog["greenhouse"] = [
            row for row in catalog["greenhouse"] if row["slug"] != removed_slug
        ]
        updated = select_targets(catalog, per_provider=10, seed="2026-W39")
        old_greenhouse = {
            target.slug for target in original if target.provider == "greenhouse"
        }
        new_greenhouse = {
            target.slug for target in updated if target.provider == "greenhouse"
        }
        self.assertEqual(len(old_greenhouse & new_greenhouse), 9)
        self.assertEqual(
            [target for target in original if target.provider != "greenhouse"],
            [target for target in updated if target.provider != "greenhouse"],
        )
        self.assertNotEqual(
            updated, select_targets(catalog, per_provider=10, seed="2026-W40")
        )


if __name__ == "__main__":
    unittest.main()
