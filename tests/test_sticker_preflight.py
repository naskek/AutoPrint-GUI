"""#87: primary preflight before any marking-sticker submit.

Fake COM/GUI contract only. No physical printers, production template/CSV,
spooler access, or PrintOut attempt.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bartender_gui


class StickerPreflightTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="AutoPrint87Tests-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def make_app(self, row_count=2, batch_size=1):
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app.cfg = {"legacy_tmp_dir": str(self.root / "legacy-absent")}
        app.REQ_COLS = list(bartender_gui.DEFAULT_REQ_COLS)
        app.csv_path = "synthetic-source.csv"
        app.csv_rows = [
            {"DM": f"SYNTHETIC-ROW-{index}", "GTIN": "04601234567893"}
            for index in range(row_count)
        ]
        app.format_combo = SimpleNamespace(get=lambda: "16x16")
        app.part_entry = SimpleNamespace(get=lambda: "TEST")
        app.skip_sticker_var = False
        app.calib_var = False
        app.cancel_requested = False
        app._catalog_print_active = 0
        app.last_batches = []
        app._require_print_capabilities = Mock(return_value=True)
        app._template_requires_manual_part = Mock(return_value=False)
        app._resolve_print_selection_for_action = Mock(
            return_value=(1, row_count, row_count)
        )
        app._get_printer = Mock(return_value="Fake Printer")
        app._get_batch_size = Mock(return_value=batch_size)
        app._bool_var = Mock(side_effect=bool)
        app._set_print_active_ui = Mock()
        app._set_progress = Mock()
        app._render_batch_buttons = Mock()
        app._preprint_stickers_for_all_batches = Mock(return_value=True)
        app._print_sticker_placeholder = Mock()
        app._record_sticker_job = Mock()
        app._mark_csv_printed = Mock()
        app._record_csv_status = Mock()
        app._pause_wait = Mock()
        app._should_stop = Mock(return_value=False)
        app._can_fast_enrich = Mock(return_value=True)
        app._enrich = Mock(side_effect=lambda row, index: {
            "_FORMAT": "16x16", "DM": row["DM"],
            "NUM": str(index), "ShortName": "Synthetic",
            "ShortGTIN": "893", "EXP_DATE": "01.01.2030",
            "PROD_DATE": "01.01.2029", "PART_NUM": "TEST",
        })
        app._apply_format_ui = Mock()
        app._ensure_printer_available_for_print = Mock(return_value=True)
        app._get_btw_for_format = Mock(return_value="disposable-test.btw")
        app._template_part_mode = Mock(return_value="manual")
        app._template_batch_report = Mock(return_value={
            "can_batch": True,
            "db_fields": ["DM"],
            "db_count": 1,
            "use_database": True,
            "has_tmp_batch_refs": True,
        })
        app._requires_database_dm = Mock(return_value=True)
        app._verify_dm_batch_database = Mock(return_value=True)
        app._dialog_flag = Mock(return_value=False)
        app._bt_batch_print = Mock()
        app._rebind_template_db = Mock(return_value=True)
        app._check_template_db = Mock(return_value=True)
        app.update_idletasks = Mock()
        app.update = Mock()
        fmt = Mock()
        app.bt = SimpleNamespace(
            open_format=Mock(return_value=fmt),
            build_gs1_btxml_print_request=Mock(return_value="<XML/>"),
            submit_gs1_btxml_batch=Mock(
                return_value=bartender_gui.BT_BATCH_SUBMITTED
            ),
        )
        return app, fmt

    def run_print(self, app):
        with (
            patch.object(bartender_gui, "BASE_DIR", str(self.root)),
            patch.object(bartender_gui.mb, "askyesno", return_value=True),
        ):
            app._print_range_one_job_via_csv()

    def test_failure_in_second_primary_preflight_blocks_every_sticker_and_main(self):
        app, fmt = self.make_app(row_count=2, batch_size=1)
        app._verify_dm_batch_database.side_effect = [True, False]
        self.run_print(app)
        self.assertEqual(app._verify_dm_batch_database.call_count, 2)
        self.assertEqual(app.bt.open_format.call_count, 2)
        self.assertEqual(fmt.Close.call_count, 2)
        app._preprint_stickers_for_all_batches.assert_not_called()
        app._print_sticker_placeholder.assert_not_called()
        app.bt.submit_gs1_btxml_batch.assert_not_called()
        app._bt_batch_print.assert_not_called()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()
        app._record_csv_status.assert_called_once()
        self.assertEqual(app._record_csv_status.call_args.args[1], "failed")
        self.assertFalse((self.root / "tmp_batch.csv").exists())

    def test_all_stickers_before_all_main_batches_without_reenrichment(self):
        app, fmt = self.make_app(row_count=2, batch_size=1)
        events = []

        def sticker(*args, **kwargs):
            events.append("all_stickers")
            self.assertEqual(app._verify_dm_batch_database.call_count, 2)
            return True

        def main(*args, **kwargs):
            events.append("primary")
            return bartender_gui.BT_BATCH_SUBMITTED

        app._preprint_stickers_for_all_batches.side_effect = sticker
        app.bt.submit_gs1_btxml_batch.side_effect = main
        self.run_print(app)
        self.assertEqual(events, ["all_stickers", "primary", "primary"])
        self.assertEqual(app.bt.build_gs1_btxml_print_request.call_count, 2)
        self.assertEqual(app._verify_dm_batch_database.call_count, 4)
        self.assertEqual(app.bt.submit_gs1_btxml_batch.call_count, 2)
        self.assertEqual(app._enrich.call_count, 2)
        self.assertEqual(app._preprint_stickers_for_all_batches.call_count, 1)
        app._print_sticker_placeholder.assert_not_called()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()
        self.assertEqual(fmt.Close.call_count, 4)
        app._mark_csv_printed.assert_called_once()

    def test_ambiguous_preprinted_sticker_blocks_main_without_fallback(self):
        app, fmt = self.make_app()
        app._preprint_stickers_for_all_batches.return_value = False
        self.run_print(app)
        app._preprint_stickers_for_all_batches.assert_called_once()
        app.bt.submit_gs1_btxml_batch.assert_not_called()
        app._bt_batch_print.assert_not_called()
        app._print_sticker_placeholder.assert_not_called()
        self.assertEqual(app._record_csv_status.call_args.args[1], "unknown")
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_live_sticker_preprint_stops_on_first_unconfirmed_attempt(self):
        app, fmt = self.make_app()
        app._preprint_stickers_for_all_batches = (
            bartender_gui.App._preprint_stickers_for_all_batches.__get__(app)
        )
        app._print_sticker_placeholder.side_effect = [False, True]
        batch_rows = [
            ([dict(app.csv_rows[0], _FORMAT="16x16")], 0),
            ([dict(app.csv_rows[1], _FORMAT="16x16")], 0),
        ]
        prepared = {(0, 1): batch_rows[0], (1, 2): batch_rows[1]}
        result = app._preprint_stickers_for_all_batches(
            [(0, 1), (1, 2)], app.csv_rows, 0, 2, 2,
            prepared=prepared, record_jobs=True,
        )
        self.assertFalse(result)
        app._record_sticker_job.assert_not_called()
        app._print_sticker_placeholder.assert_called_once()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_sticker_printout_exception_is_ambiguous_and_not_retried(self):
        app, fmt = self.make_app()
        app.cfg = {}
        app._get_sticker_printer = Mock(return_value="Fake Sticker Printer")
        app._get_client_name = Mock(return_value="")
        app._build_sticker_payload = Mock(return_value={"PART_NUM": "TEST"})
        fmt.PrintSetup = SimpleNamespace()
        fmt.PrintOut.side_effect = RuntimeError("unknown submit outcome")
        app.bt = SimpleNamespace(
            open_format=Mock(return_value=fmt),
            set_common_print_flags=Mock(),
            apply_fields=Mock(return_value=True),
        )
        with patch(
            "bartender_gui.resolve_sticker_template_path",
            return_value="disposable-sticker.btw",
        ):
            outcome = app._print_sticker_placeholder(
                1, 1, 1, 2,
                sample_row={"DM": "synthetic"},
                km_count=1, total_rolls=2,
            )
        self.assertIs(outcome, False)
        fmt.PrintOut.assert_called_once_with(False, False)
        fmt.Close.assert_called_once_with(1)
        app.bt.open_format.assert_called_once_with("disposable-sticker.btw")
        self.assertIn(
            "повтор запрещён",
            " ".join(str(c.args[0]) for c in app.logger.err.call_args_list),
        )

    def test_non_gs1_unverified_com_binding_denies_stickers(self):
        app, fmt = self.make_app()
        app._requires_database_dm.return_value = False
        app._rebind_template_db.return_value = False
        self.run_print(app)
        app._preprint_stickers_for_all_batches.assert_not_called()
        app.bt.submit_gs1_btxml_batch.assert_not_called()
        app._bt_batch_print.assert_not_called()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_gs1_dialog_unsupported_before_sticker_submit(self):
        app, fmt = self.make_app()
        app._dialog_flag.return_value = True
        self.run_print(app)
        app._preprint_stickers_for_all_batches.assert_not_called()
        app.bt.submit_gs1_btxml_batch.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_preflight_6000_rows_only_one_com_open(self):
        app, fmt = self.make_app(row_count=6000, batch_size=6000)
        result = app._preflight_primary_batches_before_stickers(
            [(0, 6000)], app.csv_rows, 0, 1
        )
        self.assertEqual(list(result), [(0, 6000)])
        self.assertEqual(len(result[(0, 6000)][0]), 6000)
        self.assertEqual(app.bt.open_format.call_count, 1)
        self.assertEqual(fmt.Close.call_count, 1)
        app.bt.submit_gs1_btxml_batch.assert_not_called()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()


if __name__ == "__main__":
    unittest.main()
