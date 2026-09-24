import ast
import inspect
import os
import tempfile
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

    def test_every_printout_call_uses_documented_two_argument_signature(self):
        tree = ast.parse(inspect.getsource(bartender_gui))
        printout_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "PrintOut"
        ]

        self.assertTrue(printout_calls)
        for call in printout_calls:
            self.assertEqual(
                len(call.args),
                2,
                f"PrintOut at line {call.lineno} must have exactly two positional arguments",
            )
            self.assertFalse(
                call.keywords,
                f"PrintOut at line {call.lineno} must not use invented keyword arguments",
            )

    def test_direct_silent_print_submits_once_with_documented_signature(self):
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app.cancel_requested = False
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace(AutoPrintAgain=True)

        result = app._bt_print(fmt, 3, False)

        self.assertTrue(result)
        fmt.PrintOut.assert_called_once_with(False, False)
        self.assertEqual(fmt.PrintSetup.IdenticalCopiesOfLabel, 3)
        self.assertFalse(fmt.AutoPrintAgain)
        self.assertFalse(fmt.PrintSetup.AutoPrintAgain)

    def test_direct_dialog_print_submits_once_with_documented_signature(self):
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app.cancel_requested = False
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace(AutoPrintAgain=True)

        result = app._bt_print(fmt, 2, True)

        self.assertTrue(result)
        fmt.PrintOut.assert_called_once_with(False, True)
        self.assertEqual(fmt.PrintSetup.IdenticalCopiesOfLabel, 2)

    def test_direct_print_exception_does_not_retry(self):
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app.cancel_requested = False
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace(AutoPrintAgain=True)
        fmt.PrintOut.side_effect = RuntimeError("COM failure after submit attempt")

        result = app._bt_print(fmt, 1, False)

        self.assertFalse(result)
        fmt.PrintOut.assert_called_once_with(False, False)
        error_lines = [
            str(call.args[0])
            for call in app.logger.err.call_args_list
            if call.args
        ]
        self.assertTrue(any("повторная отправка отключена" in line for line in error_lines))

    def test_direct_print_cancelled_before_submit(self):
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app.cancel_requested = True
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace(AutoPrintAgain=True)

        result = app._bt_print(fmt, 1, False)

        self.assertFalse(result)
        fmt.PrintOut.assert_not_called()

    @patch("win32com.client.VARIANT")
    def test_large_6000_row_batch_stages_locally_and_submits_once(self, variant):
        messages = SimpleNamespace(value=SimpleNamespace(Count=0))
        variant.return_value = messages

        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app.cfg = {}
        app.REQ_COLS = ["ShortName", "ShortGTIN", "DM", "NUM"]
        app._bt_print = Mock(return_value=True)

        rows = [
            {
                "ShortName": "TEST",
                "ShortGTIN": "123",
                "DM": f"DM-{idx}",
                "NUM": str(idx),
            }
            for idx in range(1, 6001)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tmp_batch.csv")
            written = app._write_tmp_batch_csv(rows, path, add_calib=False)
            self.assertEqual(written, path)
            with open(path, "r", encoding="utf-8-sig") as handle:
                self.assertEqual(sum(1 for _ in handle), 6001)

        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        fmt.Print.return_value = 0

        result = app._bt_batch_print(fmt, "Printer", "job-6000", 1, False)

        self.assertEqual(result, bartender_gui.BT_BATCH_SUBMITTED)
        fmt.Print.assert_called_once_with("job-6000", False, 30000, messages)
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
