import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import bartender_com


class _Databases:
    def __init__(self, *items):
        self._items = list(items)
        self.Count = len(self._items)

    def GetDatabase(self, index):
        return self._items[index - 1]


class LivePreviewDatabaseContractTests(unittest.TestCase):
    def test_preview_rebinds_only_production_text_source_via_documented_api(self):
        logger = Mock()
        bt = bartender_com.BT(logger)

        production = SimpleNamespace(
            Type=bartender_com.BT_TEXT_FILE_DATABASE,
            TextFile=SimpleNamespace(FileName=r"C:\production\tmp_batch.csv"),
        )
        lookup = SimpleNamespace(
            Type=bartender_com.BT_TEXT_FILE_DATABASE,
            TextFile=SimpleNamespace(FileName=r"C:\data\lookup.tsv"),
        )
        fmt = Mock()
        fmt.NamedSubStrings = []
        fmt.Databases = _Databases(production, lookup)
        fmt.PrintSetup = SimpleNamespace(UseDatabase=False)
        fmt.Print = Mock()
        fmt.PrintOut = Mock()

        bt.apply_fields(
            fmt,
            {
                "ShortName": "Хрен столовый",
                "ShortGTIN": "04601234567890",
                "EXP_DATE": "12.10.2026",
                "PROD_DATE": "05.10.2026",
                "PART_NUM": "12345",
                "DM": "010460123456789021ABC",
                "NUM": "7",
                "_FORMAT": "16x16",
            },
        )

        with tempfile.TemporaryDirectory() as root:
            requested = os.path.join(root, "preview_%PageNumber%.png")
            result = bt.export_preview(fmt, requested)
            expected_data = os.path.join(root, "preview_data.csv")
            expected_image = os.path.join(root, "preview_1.png")

            self.assertTrue(result)
            self.assertEqual(production.TextFile.FileName, expected_data)
            self.assertEqual(lookup.TextFile.FileName, r"C:\data\lookup.tsv")
            self.assertTrue(fmt.PrintSetup.UseDatabase)
            fmt.ExportToFile.assert_called_once_with(
                expected_image, "PNG", 1, 300, 0
            )

        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()


if __name__ == "__main__":
    unittest.main()
