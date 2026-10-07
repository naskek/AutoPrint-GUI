import datetime as dt
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

    def test_preview_restores_v324_template_export_and_never_prints(self):
        bt = bartender_com.BT(self.logger)
        fmt = Mock()

        with tempfile.TemporaryDirectory() as root:
            requested = os.path.join(root, "preview_%PageNumber%.png")
            expected = os.path.join(root, "preview_1.png")
            result = bt.export_preview(fmt, requested)

        self.assertTrue(result)
        fmt.ExportToFile.assert_called_once_with(expected, "PNG", 1, 300, 0)
        fmt.ExportPrintPreviewToImage.assert_not_called()
        fmt.ExportPrintPreviewRangeToImage.assert_not_called()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_preview_export_failure_never_submits_print(self):
        bt = bartender_com.BT(self.logger)
        fmt = Mock()
        fmt.ExportToFile.side_effect = RuntimeError("export failed")

        with tempfile.TemporaryDirectory() as root:
            result = bt.export_preview(
                fmt, os.path.join(root, "preview_%PageNumber%.png")
            )

        self.assertFalse(result)
        fmt.ExportToFile.assert_called_once()
        fmt.ExportPrintPreviewToImage.assert_not_called()
        fmt.ExportPrintPreviewRangeToImage.assert_not_called()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

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
        app._ensure_printer_available_for_print = Mock(return_value=True)

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

    @patch("bartender_gui.resolve_sticker_template_path", return_value=r"C:\\labels\\sticker60x40.btw")
    def test_marking_preview_uses_shared_payload_and_never_submits_print(self, _resolve_sticker):
        app = object.__new__(bartender_gui.App)
        app.cfg = {}
        app.logger = self.logger
        app.catalog_state = "ready"
        app.csv_path = "input.csv"
        app.csv_rows = [{"GTIN": "123", "DM": "code", "NAME": "Product"}]
        app.product_map = {
            "123": {
                "BRAND": "Brand",
                "CLIENT": "Catalog client",
                "PACKAGING": "Jar",
                "WEIGHT": "150",
            }
        }
        app._bartender_available = Mock(return_value=True)
        app._get_client_name = Mock(return_value="Manual client")
        app._show_preview_path = Mock(return_value=True)
        app._cleanup_preview_temp_dir = Mock()
        app._preview_temp_dir = None

        sample = {
            "GTIN": "123",
            "ShortName": "Short",
            "DM": "code",
            "NAME": "Product",
            "PROD_DATE": "24.09.2026",
            "EXP_DATE": "24.10.2026",
            "PART_NUM": "42001",
        }
        context = {
            "bidx": 1,
            "start": 25,
            "end": 74,
            "total": 50,
            "sample_row": sample,
            "km_count": 50,
            "total_rolls": 1,
        }
        app._resolve_sticker_preview_context = Mock(return_value=context)

        bt = Mock()
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace()
        bt.open_format.return_value = fmt
        bt.apply_fields.return_value = True
        app.bt = bt

        expected_payload = app._build_sticker_payload(
            1,
            25,
            74,
            50,
            sample_row=sample,
            km_count=50,
            total_rolls=1,
            client_name="Manual client",
        )

        with tempfile.TemporaryDirectory() as root, patch(
            "bartender_gui.tempfile.mkdtemp", return_value=root
        ):
            generated = os.path.join(root, "preview_1.png")

            def export_preview(_fmt, template_path):
                self.assertEqual(
                    template_path,
                    os.path.join(root, "preview_%PageNumber%.png"),
                )
                with open(generated, "wb") as handle:
                    handle.write(b"preview")
                return True

            bt.export_preview.side_effect = export_preview
            result = app._preview_marking_sticker(silent=True)

        self.assertTrue(result)
        bt.open_format.assert_called_once_with(r"C:\\labels\\sticker60x40.btw")
        bt.set_common_print_flags.assert_called_once_with(fmt)
        bt.apply_fields.assert_called_once_with(fmt, expected_payload)
        bt.export_preview.assert_called_once()
        app._show_preview_path.assert_called_once_with(
            generated, preserve_on_error=True
        )
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()
        fmt.Close.assert_called_once_with(1)

    def test_sticker_preview_context_matches_first_production_batch_semantics(self):
        app = object.__new__(bartender_gui.App)
        app.csv_rows = [
            {"GTIN": "123", "DM": str(index)}
            for index in range(1, 11)
        ]
        app.print_selection_mode_var = Mock()
        app.print_selection_mode_var.get.return_value = bartender_gui.PRINT_SELECTION_MODE_RANGE
        app.index_entry = Mock()
        app.index_entry.get.return_value = "3"
        app.limit_entry = Mock()
        app.limit_entry.get.return_value = "8"
        app.batch_entry = Mock()
        app.batch_entry.get.return_value = "4"
        enriched = {"GTIN": "123", "PART_NUM": "42001"}
        app._enrich = Mock(return_value=enriched)

        context = app._resolve_sticker_preview_context(silent=True)

        self.assertEqual(
            context,
            {
                "bidx": 1,
                "start": 3,
                "end": 6,
                "total": 6,
                "sample_row": enriched,
                "km_count": 4,
                "total_rolls": 2,
            },
        )
        app._enrich.assert_called_once_with(
            app.csv_rows[2],
            3,
            allow_empty_part=True,
            silent_validation=True,
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