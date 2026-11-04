"""Issue #83: strict BarTender ActiveX batch CSV pre-submit binding contract.

No real BarTender, printer, spooler or production marking codes are used.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import bartender_gui


class FakeTextFile:
    def __init__(self, current="", *, fail_set=False, ignore_set=False, fail_read=False):
        self.current = str(current)
        self.fail_set = fail_set
        self.ignore_set = ignore_set
        self.fail_read = fail_read
        self.set_count = 0

    @property
    def FileName(self):
        if self.fail_read:
            raise RuntimeError("COM read failed")
        return self.current

    @FileName.setter
    def FileName(self, value):
        self.set_count += 1
        if self.fail_set:
            raise RuntimeError("COM setter failed")
        if not self.ignore_set:
            self.current = str(value)


class FakeByRefVariant:
    def __init__(self, vartype, value):
        self.vartype = vartype
        self.value = value


class FakeDumbDispatch:
    def __new__(cls, databases):
        return databases


class FakeDatabases:
    def __init__(self, textfile, *, count=1, fail_item=False):
        self.Count = count
        self.textfile = textfile
        self.fail_item = fail_item
        self.calls = []

    def Item(self, index):
        self.calls.append(index)
        if self.fail_item:
            raise RuntimeError("COM error 12345678")
        if not isinstance(index, FakeByRefVariant):
            raise AssertionError("Databases.Item must receive a typed byref VARIANT")
        if (index.vartype, index.value) != (0x4000 | 12, 1):
            raise AssertionError("unexpected database index COM type or value")
        return SimpleNamespace(TextFile=self.textfile)


class FakePrintSetup:
    def __init__(self, *, fail_refresh=False, ignore_refresh=False):
        self.refresh = False
        self.fail_refresh = fail_refresh
        self.ignore_refresh = ignore_refresh

    @property
    def RefreshDatabases(self):
        return self.refresh

    @RefreshDatabases.setter
    def RefreshDatabases(self, value):
        if self.fail_refresh:
            raise RuntimeError("RefreshDatabases not available")
        if not self.ignore_refresh:
            self.refresh = bool(value)


class BatchBindingPreSubmitTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(bartender_gui.App)
        self.app.logger = Mock()
        # Simulate the exact pythoncom + win32com type contract on Linux CI.
        # The production method itself is called; no fake Item(1) substitution.
        pythoncom = ModuleType("pythoncom")
        pythoncom.VT_BYREF = 0x4000
        pythoncom.VT_VARIANT = 12
        win32com = ModuleType("win32com")
        win32com.__path__ = []
        client = ModuleType("win32com.client")
        client.__path__ = []
        client.VARIANT = FakeByRefVariant
        dynamic = ModuleType("win32com.client.dynamic")
        dynamic.DumbDispatch = FakeDumbDispatch
        self._com_modules = patch.dict(sys.modules, {
            "pythoncom": pythoncom,
            "win32com": win32com,
            "win32com.client": client,
            "win32com.client.dynamic": dynamic,
        })
        self._com_modules.start()
        self.addCleanup(self._com_modules.stop)

    @staticmethod
    def _fmt(path, **kwargs):
        text = FakeTextFile(
            kwargs.get("current", "old/tmp_batch.csv"),
            fail_set=kwargs.get("fail_set", False),
            ignore_set=kwargs.get("ignore_set", False),
            fail_read=kwargs.get("fail_read", False),
        )
        dbs = FakeDatabases(
            text,
            count=kwargs.get("count", 1),
            fail_item=kwargs.get("fail_item", False),
        )
        setup = FakePrintSetup(
            fail_refresh=kwargs.get("fail_refresh", False),
            ignore_refresh=kwargs.get("ignore_refresh", False),
        )
        fmt = SimpleNamespace(
            Databases=dbs, PrintSetup=setup,
            Print=Mock(), PrintOut=Mock(), Close=Mock(),
        )
        return fmt, text, dbs, setup

    def test_verified_path_and_refresh_are_positive(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\tNUM\nSYNTHETIC\t1\n", encoding="utf-8-sig")
            fmt, text, dbs, setup = self._fmt(path)

            self.assertTrue(self.app._rebind_template_db(fmt, str(path)))
            self.assertTrue(self.app._check_template_db(fmt, str(path)))
            self.assertEqual(text.FileName, str(path))
            self.assertEqual(
                [(argument.vartype, argument.value) for argument in dbs.calls],
                [(0x4000 | 12, 1)] * 3,
            )
            self.assertTrue(setup.RefreshDatabases)
            # Binding is pre-submit only; the actual calling path is covered below.
            fmt.Print.assert_not_called()
            fmt.PrintOut.assert_not_called()

    def test_already_correct_binding_still_must_accept_setter(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\nSYNTHETIC\n", encoding="utf-8-sig")
            fmt, text, _, _ = self._fmt(path, current=str(path))
            self.assertTrue(self.app._rebind_template_db(fmt, str(path)))
            self.assertEqual(text.set_count, 1)

            fmt, _, _, _ = self._fmt(path, current=str(path), fail_set=True)
            self.assertFalse(self.app._rebind_template_db(fmt, str(path)))
            fmt.Print.assert_not_called()
            fmt.PrintOut.assert_not_called()

    def test_missing_com_collection_is_not_success(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\nSYNTHETIC\n", encoding="utf-8-sig")
            # Legacy DatabaseConnections is intentionally absent. Without
            # documented Databases, a batch must not be considered verified.
            fmt = SimpleNamespace(Print=Mock(), PrintOut=Mock())
            self.assertFalse(self.app._check_template_db(fmt, str(path)))
            self.assertFalse(self.app._rebind_template_db(fmt, str(path)))
            fmt.Print.assert_not_called()
            fmt.PrintOut.assert_not_called()

    def test_no_database_multiple_databases_and_unsupported_item_are_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\nSYNTHETIC\n", encoding="utf-8-sig")
            for options in ({"count": 0}, {"count": 2}, {"fail_item": True}):
                with self.subTest(options=options):
                    fmt, text, _, _ = self._fmt(path, **options)
                    self.assertFalse(self.app._rebind_template_db(fmt, str(path)))
                    self.assertFalse(self.app._check_template_db(fmt, str(path)))
                    self.assertEqual(text.set_count, 0)
                    fmt.Print.assert_not_called()
                    fmt.PrintOut.assert_not_called()

    def test_failed_setter_stale_readback_and_unreadable_path_are_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\nSYNTHETIC\n", encoding="utf-8-sig")
            for options in (
                {"fail_set": True},
                {"ignore_set": True},
                {"fail_read": True},
            ):
                with self.subTest(options=options):
                    fmt, _, _, _ = self._fmt(path, **options)
                    self.assertFalse(self.app._rebind_template_db(fmt, str(path)))
                    fmt.Print.assert_not_called()
                    fmt.PrintOut.assert_not_called()
            stale, _, _, _ = self._fmt(path, current=str(Path(root) / "old.csv"))
            self.assertFalse(self.app._check_template_db(stale, str(path)))
            stale.Print.assert_not_called()

    def test_refresh_failure_or_false_readback_is_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\nSYNTHETIC\n", encoding="utf-8-sig")
            for options in ({"fail_refresh": True}, {"ignore_refresh": True}):
                with self.subTest(options=options):
                    fmt, _, _, _ = self._fmt(path, **options)
                    self.assertFalse(self.app._rebind_template_db(fmt, str(path)))
                    fmt.Print.assert_not_called()
                    fmt.PrintOut.assert_not_called()

    def test_missing_generated_csv_fails_before_setter(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "missing.csv"
            fmt, text, _, _ = self._fmt(path)
            self.assertFalse(self.app._rebind_template_db(fmt, str(path)))
            self.assertFalse(self.app._check_template_db(fmt, str(path)))
            self.assertEqual(text.set_count, 0)
            fmt.Print.assert_not_called()
            fmt.PrintOut.assert_not_called()


    def _run_print_path(self, row_count, *, fmt_options=None, report_override=None):
        """Exercise the production batch caller, not just its binding helper."""
        with tempfile.TemporaryDirectory() as root:
            expected = Path(root) / "tmp_batch.csv"
            fmt, _, _, _ = self._fmt(expected, **(fmt_options or {}))
            app = object.__new__(bartender_gui.App)
            app.logger = Mock()
            app.cfg = {"legacy_tmp_dir": str(Path(root) / "nonexistent")}
            app.REQ_COLS = list(bartender_gui.DEFAULT_REQ_COLS)
            app.csv_path = "synthetic-source.csv"
            app.csv_rows = [
                {"DM": f"SYNTHETIC-{i:05d}", "GTIN": "04640430910775"}
                for i in range(row_count)
            ]
            app.format_combo = SimpleNamespace(get=lambda: "16x16")
            app.part_entry = SimpleNamespace(get=lambda: "123")
            app.skip_sticker_var = True
            app.calib_var = False
            app._catalog_print_active = 0
            app._require_print_capabilities = Mock(return_value=True)
            app._template_requires_manual_part = Mock(return_value=False)
            app._resolve_print_selection_for_action = Mock(
                return_value=(1, row_count, row_count)
            )
            app._get_printer = Mock(return_value="Fake Printer")
            app._get_batch_size = Mock(return_value=row_count)
            app._bool_var = Mock(side_effect=bool)
            app._set_print_active_ui = Mock()
            app._set_progress = Mock()
            app._render_batch_buttons = Mock()
            app._preprint_stickers_for_all_batches = Mock()
            app._print_sticker_placeholder = Mock()
            app._record_sticker_job = Mock()
            app._mark_csv_printed = Mock()
            app._record_csv_status = Mock()
            app._pause_wait = Mock()
            app._should_stop = Mock(return_value=False)
            app._can_fast_enrich = Mock(return_value=True)
            app._enrich = Mock(
                return_value={"_FORMAT": "16x16", "ShortName": "SYNTHETIC"}
            )
            app._apply_format_ui = Mock()
            app._ensure_printer_available_for_print = Mock(return_value=True)
            app._get_btw_for_format = Mock(return_value="disposable-test.btw")
            app._template_part_mode = Mock(return_value="manual")
            report = {
                "can_batch": True,
                "db_fields": ["DM"],
                "template_fields": [],
                "named_fields": [],
                "db_count": 1,
                "use_database": True,
                "has_tmp_batch_refs": True,
            }
            if report_override:
                report.update(report_override)
            app._template_batch_report = Mock(return_value=report)
            app._verify_dm_batch_database = Mock(return_value=True)
            app._dialog_flag = Mock(return_value=False)
            app._bt_batch_print = Mock(
                return_value=bartender_gui.BT_BATCH_SUBMITTED
            )
            app.update_idletasks = Mock()
            app.update = Mock()
            app.bt = SimpleNamespace(open_format=Mock(return_value=fmt))
            with patch.object(bartender_gui, "BASE_DIR", root):
                app._print_range_one_job_via_csv()

            return app, fmt

    def test_real_batch_caller_denies_invalid_binding_without_submit(self):
        for options in (
            {"fail_item": True},
            {"fail_set": True},
            {"ignore_set": True},
            {"fail_read": True},
            {"fail_refresh": True},
            {"count": 0},
        ):
            with self.subTest(options=options):
                app, fmt = self._run_print_path(2, fmt_options=options)
                app._bt_batch_print.assert_not_called()
                app._preprint_stickers_for_all_batches.assert_not_called()
                app._print_sticker_placeholder.assert_not_called()
                app._mark_csv_printed.assert_not_called()
                fmt.Print.assert_not_called()
                fmt.PrintOut.assert_not_called()
                fmt.Close.assert_called_once_with(1)

    def test_db_backed_template_cannot_fall_back_to_direct_print(self):
        app, fmt = self._run_print_path(
            2, report_override={"can_batch": False}
        )
        app._bt_batch_print.assert_not_called()
        app._print_sticker_placeholder.assert_not_called()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()
        fmt.Close.assert_called_once_with(1)

    def test_real_batch_caller_sends_6000_rows_in_one_batch(self):
        app, fmt = self._run_print_path(6000)
        app._bt_batch_print.assert_called_once()
        self.assertEqual(fmt.RecordRange, "1-6000")
        self.assertTrue(fmt.UseDatabase)
        self.assertFalse(fmt.SelectRecordsAtPrint)
        app._preprint_stickers_for_all_batches.assert_not_called()
        fmt.PrintOut.assert_not_called()
        fmt.Close.assert_called_once_with(1)



if __name__ == "__main__":
    unittest.main()
