import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bartender_com
import bartender_gui


class BarTenderComBoundaryContractTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()

    @patch("bartender_com.Dispatch")
    def test_application_lifecycle_uses_documented_progid_and_no_save_quit(self, dispatch):
        com_app = dispatch.return_value
        bt = bartender_com.BT(self.logger)

        bt.start()
        dispatch.assert_called_once_with("BarTender.Application")
        self.assertFalse(com_app.Visible)

        bt.stop()
        com_app.Quit.assert_called_once_with(1)
        self.assertIsNone(bt.app)

    def test_open_format_uses_documented_formats_open_signature(self):
        bt = bartender_com.BT(self.logger)
        fmt = Mock()
        fmt.NamedSubStrings = [SimpleNamespace(Name="DM")]
        bt.app = Mock()
        bt.app.Formats.Open.return_value = fmt

        result = bt.open_format(r"C:\labels\test.btw")

        self.assertIs(result, fmt)
        bt.app.Formats.Open.assert_called_once_with(
            r"C:\labels\test.btw", False, ""
        )

    def test_named_data_sources_use_documented_setter(self):
        bt = bartender_com.BT(self.logger)
        fmt = Mock()
        fmt.NamedSubStrings = [
            SimpleNamespace(Name="DM"),
            SimpleNamespace(Name="NAME"),
        ]

        result = bt.apply_fields(
            fmt,
            {"DM": "0101", "NAME": "Test", "EXTRA": "ignored", "_FORMAT": "16x16"},
        )

        self.assertTrue(result)
        self.assertEqual(
            fmt.SetNamedSubStringValue.call_args_list,
            [
                unittest.mock.call("DM", "0101"),
                unittest.mock.call("NAME", "Test"),
            ],
        )

    @patch("win32com.client.VARIANT")
    def test_batch_success_submits_exactly_once(self, variant):
        messages = SimpleNamespace(value=SimpleNamespace(Count=0))
        variant.return_value = messages
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app._bt_print = Mock(return_value=True)
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        fmt.Print.return_value = 0

        result = app._bt_batch_print(fmt, "Printer", "job-1", 1, False)

        self.assertTrue(result)
        fmt.Print.assert_called_once_with("job-1", True, 30000, messages)
        app._bt_print.assert_not_called()

    @unittest.expectedFailure
    @patch("win32com.client.VARIANT")
    def test_timeout_must_not_resubmit_via_fallback_tracked_by_issue_13(self, variant):
        """Known contract violation. Remove expectedFailure as part of Issue #13."""
        messages = SimpleNamespace(value=SimpleNamespace(Count=0))
        variant.return_value = messages
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app._bt_print = Mock(return_value=True)
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        fmt.Print.return_value = 1  # documented btTimeout

        app._bt_batch_print(fmt, "Printer", "job-timeout", 1, False)

        fmt.Print.assert_called_once()
        app._bt_print.assert_not_called()


if __name__ == "__main__":
    unittest.main()
