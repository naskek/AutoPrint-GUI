import unittest
from unittest.mock import Mock, patch

import autoprint_main


class LicenseStartupTests(unittest.TestCase):
    def test_license_check_is_required_only_for_frozen_build(self):
        self.assertFalse(autoprint_main.license_check_required(False))
        self.assertTrue(autoprint_main.license_check_required(True))

    @patch("autoprint_main.AutoPrintApp")
    @patch("autoprint_main.ensure_license_or_exit")
    @patch.object(autoprint_main.sys, "frozen", False, create=True)
    def test_source_run_skips_license_check_and_starts_app(
        self, ensure_license, app_class
    ):
        app = app_class.return_value

        result = autoprint_main.main()

        self.assertEqual(result, 0)
        ensure_license.assert_not_called()
        app_class.assert_called_once_with()
        app.mainloop.assert_called_once_with()

    @patch("autoprint_main.AutoPrintApp")
    @patch("autoprint_main.ensure_license_or_exit", return_value=None)
    @patch.object(autoprint_main.sys, "frozen", True, create=True)
    def test_frozen_run_does_not_start_app_when_license_is_denied(
        self, ensure_license, app_class
    ):
        result = autoprint_main.main()

        self.assertEqual(result, 1)
        ensure_license.assert_called_once_with()
        app_class.assert_not_called()

    @patch("autoprint_main.AutoPrintApp")
    @patch("autoprint_main.ensure_license_or_exit", return_value=Mock())
    @patch.object(autoprint_main.sys, "frozen", True, create=True)
    def test_frozen_run_starts_app_after_license_is_accepted(
        self, ensure_license, app_class
    ):
        app = app_class.return_value

        result = autoprint_main.main()

        self.assertEqual(result, 0)
        ensure_license.assert_called_once_with()
        app_class.assert_called_once_with()
        app.mainloop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
