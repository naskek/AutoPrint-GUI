"""Issue #83: strict BarTender ActiveX batch CSV pre-submit binding contract.

No real BarTender, printer, spooler or production marking codes are used.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
        if index != 1:
            raise AssertionError("unexpected database index")
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

    def test_verified_path_and_refresh_allow_one_batch_submit(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\\tNUM\\nSYNTHETIC\\t1\\n", encoding="utf-8-sig")
            fmt, text, dbs, setup = self._fmt(path)

            self.assertTrue(self.app._rebind_template_db(fmt, str(path)))
            self.assertTrue(self.app._check_template_db(fmt, str(path)))
            self.assertEqual(text.FileName, str(path))
            self.assertEqual(dbs.calls, [1, 1, 1])
            self.assertTrue(setup.RefreshDatabases)
            # The single-print boundary is exercised only after passing preflight.
            fmt.Print("Synthetic 6000-row batch", False, 30000, None)
            fmt.Print.assert_called_once()
            fmt.PrintOut.assert_not_called()

    def test_already_correct_binding_still_must_accept_setter(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tmp_batch.csv"
            path.write_text("DM\\nSYNTHETIC\\n", encoding="utf-8-sig")
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
            path.write_text("DM\\nSYNTHETIC\\n", encoding="utf-8-sig")
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
            path.write_text("DM\\nSYNTHETIC\\n", encoding="utf-8-sig")
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
            path.write_text("DM\\nSYNTHETIC\\n", encoding="utf-8-sig")
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
            path.write_text("DM\\nSYNTHETIC\\n", encoding="utf-8-sig")
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


if __name__ == "__main__":
    unittest.main()
