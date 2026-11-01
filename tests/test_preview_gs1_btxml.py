"""Unit/contract tests for no-print BTXML GS1 RecordSet preview proof."""
from pathlib import Path
import ast
import csv
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from tools.probe_preview_gs1_btxml import (
    build_preview_xml, choose_rows, validate_response, write_one_row,
)
from tools.gs1_dm_audit import BATCH_FIELDS


def valid_dm(index: int) -> str:
    first13 = "0460123456789"
    checksum = (10 - sum(
        int(ch) * (3 if k % 2 == 0 else 1) for k, ch in enumerate(first13)
    ) % 10) % 10
    serial = f"SERIAL{index:07d}"
    assert len(serial) == 13
    return "01" + first13 + str(checksum) + "21" + serial + "\x1d93ABCD"


class BtXmlGs1PreviewProbeTests(unittest.TestCase):
    def test_select_two_distinct_codes_and_preserve_ascii_gs(self):
        with TemporaryDirectory() as dirname:
            path = Path(dirname) / "original.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, delimiter="\t", fieldnames=BATCH_FIELDS)
                writer.writeheader()
                for value in (1, 2):
                    writer.writerow({
                        "ShortName": "Хрен столовый",
                        "ShortGTIN": "890",
                        "EXP_DATE": "01.05.2027",
                        "PROD_DATE": "01.11.2026",
                        "PART_NUM": str(value),
                        "DM": valid_dm(value),
                        "NUM": str(value),
                    })
            rows = choose_rows(path)
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["DM"], rows[1]["DM"])
            self.assertIn("\x1d93", rows[0]["DM"])
            copy = Path(dirname) / "copy.csv"
            write_one_row(copy, rows[0])
            with copy.open("r", encoding="utf-8-sig", newline="") as f:
                recs = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["DM"], rows[0]["DM"])

    def test_btxml_contains_only_image_export_with_explicit_input_recordset(self):
        xml = build_preview_xml(
            Path("C:/isolated/a.btw"), Path("C:/isolated/record.csv"),
            Path("C:/isolated/png"),
        )
        tree = ET.fromstring(xml)
        self.assertIsNone(tree.find(".//Print"))
        self.assertIsNotNone(tree.find("./Command/ExportPrintPreviewToImage"))
        recordset = tree.find("./Command/ExportPrintPreviewToImage/RecordSet")
        self.assertEqual(recordset.attrib,
                         {"Name": "tmp_batch", "Type": "btTextFile", "AddIfNone": "false"})
        self.assertEqual(recordset.findtext("FileName"), str(Path("C:/isolated/record.csv")))
        self.assertEqual(recordset.findtext("Delimitation"), "btDelimTab")
        self.assertEqual(recordset.findtext("UseFieldNamesFromFirstRecord"), "true")

    def test_btxml_escapes_untrusted_paths_without_embedded_dm(self):
        xml = build_preview_xml(
            Path('C:/labels/a & b.btw'),
            Path('C:/data/x & y.csv'),
            Path('C:/output/some <folder>'),
        )
        tree = ET.fromstring(xml)
        self.assertEqual(tree.findtext("./Command/ExportPrintPreviewToImage/Format"),
                         str(Path("C:/labels/a & b.btw")))
        self.assertEqual(tree.findtext("./Command/ExportPrintPreviewToImage/RecordSet/FileName"),
                         str(Path("C:/data/x & y.csv")))
        self.assertNotIn("\x1d", xml)

    def test_response_requires_export_element_and_no_error(self):
        validate_response(
            '<Response Version="2.0"><Command Name="PreviewOnly">'
            '<ExportPrintPreviewToImage/></Command></Response>'
        )
        with self.assertRaisesRegex(RuntimeError, "No successful export"):
            validate_response("<Response><Command/></Response>")
        with self.assertRaisesRegex(RuntimeError, "contains Error"):
            validate_response(
                "<Response><Command><ExportPrintPreviewToImage/>"
                "<Error/></Command></Response>"
            )

    def test_no_print_submission_code(self):
        source = (Path(__file__).resolve().parents[1]
                  / "tools/probe_preview_gs1_btxml.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        blocked = {
            "Print", "PrintOut", "GetDatabase",
            "ExportToFile", "ExportPrintPreviewRangeToImage",
        }
        invoked = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(blocked & invoked)
        self.assertNotIn("ET.SubElement(command, \"Print\"", source)


if __name__ == "__main__":
    unittest.main()
