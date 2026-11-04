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

    def test_two_record_btxml_preview_is_explicit_no_print(self):
        from tools.smoke_83_two_row_btxml_preview import build_two_row_preview_xml
        import xml.etree.ElementTree as ET

        template = Path("D:/isolation/only_copy.btw")
        csv_path = Path("D:/isolation/tmp_batch.csv")
        output_dir = Path("D:/isolation/png")
        root = ET.fromstring(build_two_row_preview_xml(template, csv_path, output_dir))
        self.assertIsNone(root.find(".//Print"))
        self.assertIsNone(root.find(".//PrintOut"))
        self.assertEqual(len(root.findall(".//ExportPrintPreviewToImage")), 1)
        self.assertEqual(root.findtext(".//RecordSet/FileName"), str(csv_path))
        self.assertEqual(root.find(".//RecordSet").get("Name"), "tmp_batch")
        self.assertEqual(
            root.findtext(".//ExportPrintPreviewToImage/PrintSetup/RecordRange"),
            "1-2",
        )

        path = (
            Path(__file__).resolve().parents[1]
            / "tools" / "smoke_83_two_row_btxml_preview.py"
        )
        ast_tree = ast.parse(path.read_text(encoding="utf-8"))
        called = [
            node.func.attr
            for node in ast.walk(ast_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        ]
        self.assertEqual(called.count("XMLScript"), 1)
        self.assertFalse(set(called) & {
            "Print", "PrintOut", "_bt_batch_print",
            "GetDatabase", "_print_range_one_job_via_csv",
        })

    def test_named_diagnostic_single_vendor_item_and_no_submit(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "tools" / "diagnose_83_named_lookup.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        ]
        self.assertEqual(calls.count("Item"), 1)
        self.assertFalse(set(calls) & {
            "Print", "PrintOut", "GetDatabase", "XMLScript",
            "ExportPrintPreviewToImage", "ExportPrintPreviewRangeToImage",
            "ExportToFile", "_bt_batch_print", "_print_rows_direct",
        })

    def test_named_diagnostic_configuration_name_parser(self):
        from tools.diagnose_83_named_lookup import _names_in_configuration
        data = (
            '<Root><RecordSet Name="tmp_batch"/>'
            '<Database><Name>second_source</Name></Database></Root>'
        )
        self.assertEqual(
            _names_in_configuration(data), {"tmp_batch", "second_source"}
        )

    def test_stage_diagnostic_only_uses_one_database_lookup_and_no_print(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "tools" / "diagnose_83_com_dispatch.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        ]
        self.assertEqual(called.count("_batch_database_one"), 1)
        self.assertFalse(set(called) & {
            "Print", "PrintOut", "GetDatabase", "XMLScript",
            "ExportPrintPreviewToImage", "ExportPrintPreviewRangeToImage",
            "ExportToFile", "_bt_batch_print", "_print_rows_direct",
        })

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
