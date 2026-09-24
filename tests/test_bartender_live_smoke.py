import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tools import bartender_live_smoke


class BarTenderLiveSmokeHarnessTests(unittest.TestCase):
    def test_btw_inspection_never_submits_print(self):
        app = Mock()
        fmt = Mock()
        fmt.PrintSetup = SimpleNamespace(
            IdenticalCopiesOfLabel=1,
            Printer="Test Printer",
        )
        fmt.NamedSubStrings = [
            SimpleNamespace(Name="DM"),
            SimpleNamespace(Name="NUM"),
        ]
        fmt.Databases = SimpleNamespace(Count=1)
        app.Formats.Open.return_value = fmt

        with tempfile.NamedTemporaryFile(suffix=".btw", delete=False) as handle:
            path = handle.name
        try:
            bartender_live_smoke._inspect_format(app, path)
        finally:
            os.unlink(path)

        app.Formats.Open.assert_called_once_with(os.path.abspath(path), False, "")
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()
        fmt.Close.assert_called_once_with(
            bartender_live_smoke.BT_DO_NOT_SAVE_CHANGES
        )
        self.assertEqual(fmt.PrintSetup.IdenticalCopiesOfLabel, 1)

    def test_btw_inspection_rejects_missing_path_before_open(self):
        app = Mock()
        missing = os.path.join(
            tempfile.gettempdir(),
            "autoprint-live-smoke-definitely-missing.btw",
        )
        try:
            os.unlink(missing)
        except FileNotFoundError:
            pass

        with self.assertRaisesRegex(RuntimeError, "BTW does not exist"):
            bartender_live_smoke._inspect_format(app, missing)

        app.Formats.Open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
