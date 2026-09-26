"""Release tags must select exactly one independently versioned package."""

import tempfile
import unittest
from pathlib import Path

from _bootstrap import ROOT  # noqa: F401

from scripts.prepare_release import prepare_release


class ReleaseSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.destination = self.root / "release-dist"
        for name in ("swe_internship_scraper-1.0.0rc2", "swe_scraper_icims-0.1.0"):
            (self.dist / f"{name}-py3-none-any.whl").write_bytes(b"wheel")
            (self.dist / f"{name}.tar.gz").write_bytes(b"sdist")

    def test_core_tag_excludes_already_published_plugin(self):
        self.assertEqual(
            prepare_release(self.dist, self.destination, "refs/tags/v1.0.0rc2"), "core"
        )
        self.assertEqual(
            sorted(path.name for path in self.destination.iterdir()),
            [
                "swe_internship_scraper-1.0.0rc2-py3-none-any.whl",
                "swe_internship_scraper-1.0.0rc2.tar.gz",
            ],
        )

    def test_plugin_tag_excludes_core(self):
        self.assertEqual(
            prepare_release(self.dist, self.destination, "refs/tags/icims-v0.1.0"), "icims"
        )
        self.assertTrue(
            all(
                path.name.startswith("swe_scraper_icims-")
                for path in self.destination.iterdir()
            )
        )

    def test_manual_build_selects_requested_package(self):
        self.assertEqual(
            prepare_release(self.dist, self.destination, "refs/heads/main", "icims"),
            "icims",
        )

    def test_invalid_or_mismatched_tag_never_stages_artifacts(self):
        for ref in ("refs/tags/v1.0.0", "refs/tags/icims-v0.2.0", "refs/tags/unknown"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                prepare_release(self.dist, self.destination, ref)
            self.assertFalse(self.destination.exists())

    def test_missing_sdist_or_ambiguous_wheel_fails(self):
        (self.dist / "swe_scraper_icims-0.1.0.tar.gz").unlink()
        with self.assertRaises(ValueError):
            prepare_release(self.dist, self.destination, "refs/tags/icims-v0.1.0")
        (self.dist / "swe_internship_scraper-1.0.0-py3-none-any.whl").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            prepare_release(self.dist, self.destination, "refs/tags/v1.0.0rc2")

    def test_existing_artifacts_and_unknown_package_rejected(self):
        with self.assertRaises(ValueError):
            prepare_release(self.dist, self.destination, "refs/heads/main", "unknown")
        self.destination.mkdir()
        existing = self.destination / "stale.whl"
        existing.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            prepare_release(self.dist, self.destination, "refs/tags/v1.0.0rc2")
        self.assertEqual(existing.read_bytes(), b"keep")
