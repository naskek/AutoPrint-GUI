from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from build_tools.build_exe import _write_windows_version_file
from updater import is_newer_version, parse_version


class VersionTests(unittest.TestCase):
    def test_parse_semver(self):
        self.assertEqual((3, 2, 0, 0), parse_version("3.2.0"))
        self.assertEqual((3, 2, 1, 0), parse_version("v3.2.1"))

    def test_version_comparison(self):
        self.assertTrue(is_newer_version("3.2.1", "3.2.0"))
        self.assertTrue(is_newer_version("3.3.0", "3.2.9"))
        self.assertFalse(is_newer_version("3.2.0", "3.2.0"))
        self.assertFalse(is_newer_version("3.1.9", "3.2.0"))

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_version("3.2")
        with self.assertRaises(ValueError):
            parse_version("latest")

    def test_windows_version_resource_comes_from_app_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_windows_version_file(Path(tmp), "3.2.0")
            text = path.read_text(encoding="utf-8")
            self.assertIn("filevers=(3, 2, 0, 0)", text)
            self.assertIn("ProductVersion', '3.2.0'", text)


if __name__ == "__main__":
    unittest.main()
