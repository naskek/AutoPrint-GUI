import unittest
from unittest.mock import Mock, patch

import bartender_gui


class LicenseStartupTests(unittest.TestCase):
    @patch("bartender_gui.App")
    @patch("bartender_gui.enum_printers")
    @patch("bartender_gui.BT")
    @patch("bartender_gui.ensure_license_or_exit", return_value=None)
    def test_main_does_not_start_app_com_or_printers_when_license_is_denied(
        self,
        ensure_license,
        bt_class,
        enum_printers,
        app_class,
    ):
        result = bartender_gui.main()

        self.assertEqual(result, 1)
        ensure_license.assert_called_once_with()
        app_class.assert_not_called()
        bt_class.assert_not_called()
        enum_printers.assert_not_called()

    @patch("bartender_gui.App")
    @patch("bartender_gui.ensure_license_or_exit", return_value=Mock())
    def test_main_creates_app_after_license_is_accepted(self, ensure_license, app_class):
        app = app_class.return_value

        result = bartender_gui.main()

        self.assertEqual(result, 0)
        ensure_license.assert_called_once_with()
        app_class.assert_called_once_with()
        app.mainloop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
