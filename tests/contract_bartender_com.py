import datetime as dt
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

    @patch("bartender_gui.resolve_sticker_template_path", return_value=r"C:\\labels\\sticker60x40.btw")
    def test_manual_part_num_reaches_sticker_named_data_source(self, _resolve_sticker):
        app = object.__new__(bartender_gui.App)
        app.cfg = {}
        app.logger = self.logger
        app._require_print_capabilities = Mock(return_value=True)
        app._get_sticker_printer = Mock(return_value="Sticker Printer")
        app._get_client_name = Mock(return_value="")
        app._get_prod_date = Mock(return_value=dt.date(2026, 9, 24))
        app._get_exp_days = Mock(return_value=None)
        app._mark_part_entry = Mock()
        app._bool_var = Mock(return_value=False)
        app.skip_sticker_var = False
        app.format_combo = Mock()
        app.format_combo.get.return_value = "Auto"
        app.part_entry = Mock()
        app.part_entry.get.return_value = "42001"
        app.product_map = {
            "123": {
                "TEMPLATE_ID": "16x16",
                "SHELF": {},
            }
        }
        app._template_part_mode = Mock(return_value="manual")

        bt = bartender_com.BT(self.logger)
        fmt = Mock()
        fmt.NamedSubStrings = [SimpleNamespace(Name="PART_NUM")]
        fmt.PrintSetup = SimpleNamespace()
        bt.app = Mock()
        bt.app.Formats.Open.return_value = fmt
        app.bt = bt

        printed = app._preprint_stickers_for_all_batches(
            [(0, 1)],
            [{"DM": "code", "GTIN": "123", "NAME": "Product"}],
            0,
            1,
            1,
            record_jobs=False,
        )

        self.assertTrue(printed)
        app.part_entry.get.assert_called()
        app._template_part_mode.assert_called()
        fmt.SetNamedSubStringValue.assert_called_once_with("PART_NUM", "42001")
        fmt.PrintOut.assert_called_once_with(False, False)
        fmt.Close.assert_called_once_with(1)

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

        self.assertEqual(result, bartender_gui.BT_BATCH_SUBMITTED)
        fmt.Print.assert_called_once_with("job-1", False, 30000, messages)
        app._bt_print.assert_not_called()
        self.assertFalse(fmt.AutoPrintAgain)
        self.assertFalse(fmt.PrintSetup.AutoPrintAgain)

    @patch("win32com.client.VARIANT")
    def test_timeout_does_not_resubmit_via_fallback(self, variant):
        messages = SimpleNamespace(value=SimpleNamespace(Count=0))
        variant.return_value = messages
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app._bt_print = Mock(return_value=True)
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        fmt.Print.return_value = 1  # documented btTimeout

        result = app._bt_batch_print(fmt, "Printer", "job-timeout", 1, False)

        self.assertEqual(result, bartender_gui.BT_BATCH_AMBIGUOUS)
        fmt.Print.assert_called_once_with("job-timeout", False, 30000, messages)
        app._bt_print.assert_not_called()
        self.assertFalse(fmt.AutoPrintAgain)
        app.logger.err.assert_not_called()
        timeout_logs = [
            str(call.args[0])
            for call in app.logger.log.call_args_list
            if call.args
        ]
        self.assertTrue(any("btTimeout (1)" in line for line in timeout_logs))
        self.assertTrue(any("неоднозначным" in line for line in timeout_logs))

    @patch("win32com.client.VARIANT")
    def test_hard_failure_does_not_resubmit_via_fallback(self, variant):
        messages = SimpleNamespace(value=SimpleNamespace(Count=0))
        variant.return_value = messages
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app._bt_print = Mock(return_value=True)
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        fmt.Print.return_value = 2  # documented btFailure

        result = app._bt_batch_print(fmt, "Printer", "job-failure", 1, False)

        self.assertEqual(result, bartender_gui.BT_BATCH_FAILED)
        fmt.Print.assert_called_once_with("job-failure", False, 30000, messages)
        app._bt_print.assert_not_called()

    @patch("win32com.client.VARIANT")
    def test_print_exception_is_ambiguous_and_does_not_retry(self, variant):
        messages = SimpleNamespace(value=SimpleNamespace(Count=0))
        variant.return_value = messages
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app._bt_print = Mock(return_value=True)
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        fmt.Print.side_effect = RuntimeError("COM timeout")

        result = app._bt_batch_print(fmt, "Printer", "job-exception", 1, False)

        self.assertEqual(result, bartender_gui.BT_BATCH_AMBIGUOUS)
        fmt.Print.assert_called_once_with("job-exception", False, 30000, messages)
        app._bt_print.assert_not_called()


    @patch("win32com.client.VARIANT")
    def test_malformed_result_is_ambiguous_and_does_not_retry(self, variant):
        messages = SimpleNamespace(value=SimpleNamespace(Count=0))
        variant.return_value = messages
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app._bt_print = Mock(return_value=True)
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        fmt.Print.return_value = object()

        result = app._bt_batch_print(fmt, "Printer", "job-malformed", 1, False)

        self.assertEqual(result, bartender_gui.BT_BATCH_AMBIGUOUS)
        fmt.Print.assert_called_once_with("job-malformed", False, 30000, messages)
        app._bt_print.assert_not_called()

    @patch("win32com.client.VARIANT", side_effect=RuntimeError("VARIANT unavailable"))
    def test_pre_submit_messages_setup_failure_stops_without_printout_fallback(self, _variant):
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app._bt_print = Mock(return_value=True)
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()

        result = app._bt_batch_print(fmt, "Printer", "job-pre-submit", 1, False)

        self.assertEqual(result, bartender_gui.BT_BATCH_FAILED)
        fmt.Print.assert_not_called()
        app._bt_print.assert_not_called()


if __name__ == "__main__":
    unittest.main()
