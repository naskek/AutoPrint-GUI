import unittest
from unittest.mock import Mock, patch

import autoprint_main


class LicenseStartupTests(unittest.TestCase):
    @patch("autoprint_main.AutoPrintApp")
    @patch("autoprint_main.ensure_license_or_exit", return_value=None)
    def test_main_does_not_start_app_when_license_is_denied(
        self, ensure_license, app_class
    ):
        result = autoprint_main.main()

        self.assertEqual(result, 1)
        ensure_license.assert_called_once_with()
        app_class.assert_not_called()

    @patch("autoprint_main.AutoPrintApp")
    @patch("autoprint_main.ensure_license_or_exit", return_value=Mock())
    def test_main_creates_app_after_license_is_accepted(self, ensure_license, app_class):
        app = app_class.return_value

        result = autoprint_main.main()

        self.assertEqual(result, 0)
        ensure_license.assert_called_once_with()
        app_class.assert_called_once_with()
        app.mainloop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
