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
        fmt.PageSetup = SimpleNamespace(
            MediaHandling=SimpleNamespace(
                Action=0,
                Occurrence=0,
                NumberOfLabels=1,
            )
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
        self.assertEqual(fmt.PageSetup.MediaHandling.Action, 0)
        self.assertEqual(fmt.PageSetup.MediaHandling.Occurrence, 0)
        self.assertEqual(fmt.PageSetup.MediaHandling.NumberOfLabels, 1)

    def test_media_handling_probe_is_read_only_and_decodes_values(self):
        fmt = Mock()
        fmt.PageSetup = SimpleNamespace(
            MediaHandling=SimpleNamespace(
                Action=4,
                Occurrence=2,
                NumberOfLabels=7,
            )
        )

        result = bartender_live_smoke._inspect_media_handling(fmt)

        self.assertEqual(
            result,
            {
                "action": 4,
                "action_name": "btMHCut",
                "occurrence": 2,
                "occurrence_name": "btMHAfterEveryRecord",
                "number_of_labels": 7,
            },
        )
        self.assertEqual(fmt.PageSetup.MediaHandling.Action, 4)
        self.assertEqual(fmt.PageSetup.MediaHandling.Occurrence, 2)
        self.assertEqual(fmt.PageSetup.MediaHandling.NumberOfLabels, 7)
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_media_handling_enum_tables_match_documented_values(self):
        self.assertEqual(
            bartender_live_smoke.MEDIA_ACTION_NAMES,
            {
                0: "btMHDriverSettings",
                1: "btMHPageBreak",
                2: "btMHPause",
                3: "btMHWaitForLabelTakenSensor",
                4: "btMHCut",
                5: "btMHCutAndPause",
            },
        )
        self.assertEqual(
            bartender_live_smoke.MEDIA_OCCURRENCE_NAMES,
            {
                0: "btMHAtEndOfPrintJob",
                1: "btMHAfterEveryPage",
                2: "btMHAfterEveryRecord",
                3: "btMHAfterIdenticalCopies",
                4: "btMHAfterDataChange",
                5: "btMHAfterNumberOfLabels",
            },
        )

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
