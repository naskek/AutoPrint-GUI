"""No-print safety tests for opt-in exact-head Issue #83 COM harness."""
import ast
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.smoke_83_exact_head import FIELDS, _synthetic_rows, _write_synthetic_csv


class Issue83ExactHeadHarnessTests(unittest.TestCase):
    def test_synthetic_csv_has_two_distinct_gs1_sources(self):
        rows = _synthetic_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), set(FIELDS))
        self.assertEqual(rows[0]["PART_NUM"], "TEST-1")
        self.assertEqual(rows[1]["PART_NUM"], "TEST-2")
        self.assertNotEqual(rows[0]["DM"], rows[1]["DM"])
        for index, row in enumerate(rows, 1):
            self.assertEqual(row["DM"], "010460123456789321"
                             + f"SYNTH{index:08d}" + "\x1d93ABCD")
        with TemporaryDirectory() as root:
            csv_path = Path(root) / "tmp_batch.csv"
            _write_synthetic_csv(csv_path, rows)
            with csv_path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter="\t")
                self.assertEqual(tuple(reader.fieldnames), FIELDS)
                self.assertEqual(list(reader), rows)

    def test_no_print_submit_or_preview_com_calls_in_diagnostic(self):
        path = Path(__file__).resolve().parents[1] / "tools" / "smoke_83_exact_head.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue({
            "_rebind_template_db", "_verify_dm_batch_database",
            "_check_template_db", "_template_batch_report",
        }.issubset(called))
        self.assertFalse(called & {
            "Print", "PrintOut", "XMLScript",
            "ExportPrintPreviewToImage",
            "ExportPrintPreviewRangeToImage",
            "ExportToFile",
            "_bt_batch_print",
            "_print_range_one_job_via_csv",
            "_print_rows_direct",
            "_print_sticker_placeholder",
        })


if __name__ == "__main__":
    unittest.main()
