"""Contract tests: GS1 record preview experiment never submits a print job."""
import ast
import csv
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from tools.gs1_dm_audit import BATCH_FIELDS
from tools.probe_preview_gs1_record_range import (
    select_two_real_records, export_one_record,
)


def valid_dm(n):
    gtin13 = "0460123456789"
    check = (10 - sum(
        int(ch) * (3 if index % 2 == 0 else 1)
        for index, ch in enumerate(gtin13)
    ) % 10) % 10
    serial = f"SERIAL{n:07d}"
    assert len(serial) == 13
    return f"01{gtin13}{check}21{serial}\x1d93ABCD"


class PrintPreviewRecordProbeTests(unittest.TestCase):
    def test_row_indexes_preserve_six_calibration_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "batch.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, delimiter="\t", fieldnames=BATCH_FIELDS)
                writer.writeheader()
                for _ in range(6):
                    writer.writerow({key: ("X" if key != "ShortGTIN" and key != "NUM"
                                         else "000" if key == "ShortGTIN" else "1")
                                     for key in BATCH_FIELDS})
                for n in (1, 2):
                    writer.writerow({
                        "DM": valid_dm(n),
                        "NUM": str(n),
                        "ShortName": "Test",
                        "ShortGTIN": "890",
                        "EXP_DATE": "01.05.2027",
                        "PROD_DATE": "01.11.2026",
                        "PART_NUM": str(n),
                    })
            self.assertEqual(select_two_real_records(path), [7, 8])

    def test_documented_range_export_and_database_flags(self):
        fmt = Mock()
        fmt.ExportPrintPreviewRangeToImage.return_value = 0
        export_one_record(fmt, 7, Path("isolated"), "msgs")
        self.assertIs(fmt.UseDatabase, True)
        self.assertIs(fmt.PrintSetup.UseDatabase, True)
        self.assertIs(fmt.SelectRecordsAtPrint, False)
        self.assertEqual(fmt.RecordRange, "7")
        fmt.ExportPrintPreviewRangeToImage.assert_called_once_with(
            "1", "isolated", "preview_%PageNumber%.png", "PNG",
            4, 300, 0xFFFFFF, 1, False, False, "msgs",
        )
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_failed_export_does_not_retry_or_print(self):
        fmt = Mock()
        fmt.ExportPrintPreviewRangeToImage.return_value = 2
        with self.assertRaisesRegex(RuntimeError, "preview returned failure"):
            export_one_record(fmt, 8, Path("isolated"), "msgs")
        fmt.ExportPrintPreviewRangeToImage.assert_called_once()
        fmt.Print.assert_not_called()
        fmt.PrintOut.assert_not_called()

    def test_probe_has_no_hidden_print_or_database_mutation(self):
        path = Path(__file__).resolve().parents[1] / "tools" / "probe_preview_gs1_record_range.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = {"Print", "PrintOut", "GetDatabase", "SetNamedSubStringValue",
                     "UsedNamesSampleData", "ExportToFile"}
        invoked = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(forbidden & invoked)


if __name__ == "__main__":
    unittest.main()
