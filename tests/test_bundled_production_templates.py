"""#63: production BTW assets and default 16x16 part contract.

These tests intentionally fail on the historical restored BTW files.
The PR must stay draft until verified current production GS1 binaries are
committed, and exact-hash tests pass without weakening the expectation.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bartender_gui
from build_tools.build_exe import REQUIRED_BUNDLED_TEMPLATES


ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "templates"
# The operator used this exact file in the live #60+#70+#64 Windows GS1
# preview smoke on 2026-11-01. Older #63 hashes from 2026-10-05
# predate that verified GS1 template and MUST NOT replace it.
VERIFIED_GS1_16X16_SHA256 = (
    "ed1b6c64c5a24642db28dbff8862b08288f308e5f613e515e460cb7b94af3bb0"
)


class ProductionBundledTemplatesTests(unittest.TestCase):
    def test_all_required_templates_are_packaged(self):
        self.assertEqual(
            set(REQUIRED_BUNDLED_TEMPLATES),
            {"16x16.btw", "30x20.btw", "58x60.btw", "sticker60x40.btw"},
        )
        missing = [
            name for name in REQUIRED_BUNDLED_TEMPLATES
            if not (BUNDLED / name).is_file()
        ]
        self.assertFalse(missing, f"Production bundled BTW missing: {missing}")

    def test_16x16_is_the_operator_verified_gs1_btw(self):
        target = BUNDLED / "16x16.btw"
        self.assertTrue(target.is_file(), f"Missing {target}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        self.assertEqual(
            actual, VERIFIED_GS1_16X16_SHA256,
            "Do not ship the historical 16x16 BTW: it predates the "
            "validated GS1 01/21/93 rendering contract.",
        )

    def test_fresh_default_16x16_requires_manual_part(self):
        app = Mock()
        app.cfg = {}
        templates = bartender_gui.App._default_label_templates(app)
        self.assertEqual(templates["16x16"]["part_mode"], "manual")
        self.assertEqual(templates["30x20"]["part_mode"], "manual")
        self.assertEqual(templates["58x60"]["part_mode"], "manual")

    def test_existing_blank_mode_override_is_not_changed_by_new_default(self):
        app = Mock()
        app.cfg = {
            "label_templates": {
                "16x16": {"id": "16x16", "part_mode": "blank"}
            }
        }
        app._default_label_templates.side_effect = (
            lambda: bartender_gui.App._default_label_templates(app)
        )
        app._normalize_template_id.side_effect = (
            lambda value: str(value or "").strip()
        )
        app._sanitize_template_filename.side_effect = (
            lambda value, _template_id: str(value or "")
        )
        app._normalize_part_mode.side_effect = (
            lambda mode: bartender_gui.App._normalize_part_mode(app, mode)
        )
        resolved = bartender_gui.App._load_label_templates_from_cfg(app)
        self.assertEqual(resolved["16x16"]["part_mode"], "blank")
        self.assertEqual(resolved["30x20"]["part_mode"], "manual")

    def test_user_btw_wins_over_bundled_and_bundled_over_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user = root / "user"
            bundle = root / "bundled"
            legacy = root / "old-install"
            for directory in (user, bundle, legacy):
                directory.mkdir()
            for directory, data in (
                (user, b"user-custom-btw"),
                (bundle, b"verified-bundled-btw"),
                (legacy, b"historical-legacy-btw"),
            ):
                (directory / "16x16.btw").write_bytes(data)
            with patch.object(bartender_gui, "TEMPLATES_DIR", str(user)), \
                 patch.object(bartender_gui, "BUNDLED_TEMPLATES_DIR", str(bundle)), \
                 patch.object(bartender_gui, "BASE_DIR", str(legacy)):
                path = bartender_gui.default_template_path("16x16.btw")
                self.assertEqual(Path(path), user / "16x16.btw")
                (user / "16x16.btw").unlink()
                self.assertEqual(
                    Path(bartender_gui.default_template_path("16x16.btw")),
                    bundle / "16x16.btw",
                )
                (bundle / "16x16.btw").unlink()
                self.assertEqual(
                    Path(bartender_gui.default_template_path("16x16.btw")),
                    legacy / "16x16.btw",
                )


if __name__ == "__main__":
    unittest.main()
