import csv
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


class LivePreviewRuntimeDataTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()
        self.bt = bartender_com.BT(self.logger)

    @staticmethod
    def _runtime_row():
        return {
            "ShortName": "Хрен столовый",
            "ShortGTIN": "04601234567890",
            "EXP_DATE": "12.10.2026",
            "PROD_DATE": "05.10.2026",
            "PART_NUM": "12345",
            "DM": "010460123456789021ABC",
            "NUM": "7",
            "_FORMAT": "16x16",
            "IGNORED": "not part of production batch contract",
        }

    def test_database_backed_preview_uses_one_row_temp_source(self):
        text_file = SimpleNamespace(FileName=r"C:\production\tmp_batch.csv")
        database = SimpleNamespace(
            Type=bartender_com.BT_TEXT_FILE_DATABASE,
            TextFile=text_file,
        )
        fmt = Mock()
        fmt.NamedSubStrings = []
        fmt.Databases = _Databases(database)
        fmt.PrintSetup = SimpleNamespace(UseDatabase=False)
        fmt.Print = Mock()
        fmt.PrintOut = Mock()

        self.bt.apply_fields(fmt, self._runtime_row())

        captured = {}

        def export(output_path, file_type, colors, resolution, save_options):
            captured["output_path"] = output_path
            captured["bound_path"] = database.TextFile.FileName
            with open(database.TextFile.FileName, "r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter="\t")
                captured["fieldnames"] = reader.fieldnames
                captured["rows"] = list(reader)

        fmt.ExportToFile.side_effect = export

        with tempfile.TemporaryDirectory() as root:
            requested = os.path.join(root, "preview_%PageNumber%.png")
            self.assertTrue(self.bt.export_preview(fmt, requested))

            self.assertEqual(
                os.path.dirname(captured["bound_path"]),
                os.path.abspath(root),
            )
            self.assertEqual(
                os.path.basename(captured["bound_path"]),
                "preview_data.csv",
            )
            self.assertNotEqual(
                os.path.normcase(captured["bound_path"]),
                os.path.normcase(r"C:\production\tmp_batch.csv"),
            )

        self.assertEqual(
            captured["fieldnames"],
            list(bartender_com.PREVIEW_DATA_FIELDS),
        )
        self.assertEqual(len(captured["rows"]), 1)
        row = captured["rows"][0]
        self.assertEqual(row["ShortName"], "Хрен столовый")
        self.assertEqual(row["PROD_DATE"], "05.10.2026")
        self.assertEqual(row["PART_NUM"], "12345")
        self.assertEqual(row["NUM"], "7")
        self.assertNotIn("IGNORED", row)
        self.assertTrue(fmt.PrintSetup.UseDatabase)
        fmt.ExportToFile.assert_called_once_with(
            captured["output_path"], "PNG", 1, 300, 0
        )
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()
        self.assertIsNone(self.bt._preview_fields)

    def test_unrelated_text_database_is_not_rebound(self):
        database = SimpleNamespace(
            Type=bartender_com.BT_TEXT_FILE_DATABASE,
            TextFile=SimpleNamespace(FileName=r"C:\data\lookup.tsv"),
        )
        fmt = Mock()
        fmt.NamedSubStrings = []
        fmt.Databases = _Databases(database)
        fmt.PrintSetup = SimpleNamespace(UseDatabase=False)

        self.bt.apply_fields(fmt, self._runtime_row())

        with tempfile.TemporaryDirectory() as root:
            requested = os.path.join(root, "preview_%PageNumber%.png")
            self.assertTrue(self.bt.export_preview(fmt, requested))

        self.assertEqual(database.TextFile.FileName, r"C:\data\lookup.tsv")
        self.assertFalse(fmt.PrintSetup.UseDatabase)
        fmt.ExportToFile.assert_called_once()

    def test_non_text_database_is_not_rebound(self):
        database = SimpleNamespace(
            Type=3,
            TextFile=SimpleNamespace(FileName=r"C:\data\source.xlsx"),
        )
        fmt = Mock()
        fmt.NamedSubStrings = []
        fmt.Databases = _Databases(database)
        fmt.PrintSetup = SimpleNamespace(UseDatabase=False)

        self.bt.apply_fields(fmt, self._runtime_row())

        with tempfile.TemporaryDirectory() as root:
            requested = os.path.join(root, "preview_%PageNumber%.png")
            self.assertTrue(self.bt.export_preview(fmt, requested))

        self.assertEqual(database.TextFile.FileName, r"C:\data\source.xlsx")
        self.assertFalse(fmt.PrintSetup.UseDatabase)
        fmt.ExportToFile.assert_called_once()

    def test_opening_new_format_clears_stale_preview_row(self):
        self.bt._preview_fields = self._runtime_row()
        opened = Mock()
        opened.NamedSubStrings = []
        self.bt.app = SimpleNamespace(
            Formats=SimpleNamespace(Open=Mock(return_value=opened))
        )

        result = self.bt.open_format(r"C:\labels\16x16.btw")

        self.assertIs(result, opened)
        self.assertIsNone(self.bt._preview_fields)


if __name__ == "__main__":
    unittest.main()
