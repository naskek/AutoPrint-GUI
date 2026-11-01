"""Regressions for issue #60 GS1 BTXML *normal-label* runtime preview."""
import csv
import hashlib
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bartender_com


class _Format:
    def __init__(self, db=True, gs1=True, db_count=1):
        self.Databases = SimpleNamespace(Count=db_count)
        self.db = db
        self.gs1 = gs1

    def UsedNames(self, kind, separator):
        if kind == 1:
            fields = (
                "ShortName", "ShortGTIN", "PROD_DATE", "PART_NUM", "DM", "NUM"
            ) if self.db else ("DM",)
            return "\n".join("tmp_batch.tmp_batch." + f for f in fields)
        if kind == 0:
            return "DM\nGS1_GTIN\nGS1_SERIAL\nGS1_CRYPTO" if self.gs1 else "DM"
        raise AssertionError("Unknown UsedNames kind")


class GS1BtXmlLivePreviewTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.bt = bartender_com.BT(self.log)

    @staticmethod
    def _row():
        return {
            "ShortName": "Хрен столовый",
            "ShortGTIN": "520",
            "EXP_DATE": "01.05.2027",
            "PROD_DATE": "31.10.2026",
            "PART_NUM": "622",
            "DM": "010460123456789021SERIAL0000001\x1d93ABCD",
            "NUM": "1",
        }

    def test_gs1_database_contract_is_required(self):
        self.assertTrue(self.bt.supports_gs1_btxml_preview(_Format()))
        self.assertFalse(self.bt.supports_gs1_btxml_preview(_Format(db=False)))
        self.assertFalse(self.bt.supports_gs1_btxml_preview(_Format(gs1=False)))
        self.assertFalse(self.bt.supports_gs1_btxml_preview(_Format(db_count=2)))

    def test_xmlscript_contains_only_record_preview_and_explicit_source(self):
        xml = self.bt._build_gs1_btxml_preview_request(
            "C:/temp/isolated.btw", "C:/temp/data.csv", "C:/temp/out"
        )
        root = ET.fromstring(xml)
        self.assertIsNone(root.find(".//Print"))
        export = root.find("./Command/ExportPrintPreviewToImage")
        self.assertIsNotNone(export)
        self.assertEqual(export.attrib["ReturnImageInResponse"], "false")
        self.assertEqual(export.findtext("RecordSet/FileName"),
                         "C:/temp/data.csv")
        self.assertEqual(export.find("RecordSet").attrib["AddIfNone"], "false")
        self.assertEqual(export.findtext("PrintSetup/UseDatabase"), "true")
        self.assertEqual(export.findtext("PrintSetup/RecordRange"), "1")
        self.assertEqual(export.findtext("PrintSetup/AutoPrintAgain"), "false")
        self.assertEqual(export.findtext("PrintSetup/EnablePrompting"), "false")
        self.assertEqual(export.findtext("FileNameTemplate"),
                         "preview_%PageNumber%.png")

    def test_response_fails_closed_on_errors_and_missing_export(self):
        self.assertTrue(self.bt._btxml_preview_response_ok(
            '<Response><Command><ExportPrintPreviewToImage/></Command></Response>'
        ))
        for response in (
            "",
            '<Response><Command/></Response>',
            '<Response><Command><ExportPrintPreviewToImage/>'
            '<Message Severity="Error"/></Command></Response>',
            '<Response><Command><ExportPrintPreviewToImage/>'
            '<Error/></Command></Response>',
            '<Secret>bad</Secret>',
        ):
            self.assertFalse(self.bt._btxml_preview_response_ok(response))

    @patch("win32com.client.VARIANT")
    def test_writes_exact_one_row_and_never_submits_a_print(self, variant):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "operator.btw"
            original.write_bytes(b"immutable BTW")
            checksum = hashlib.sha256(original.read_bytes()).digest()
            target = Path(root) / "preview" / "preview_%PageNumber%.png"
            document = Mock()

            def export(xml, source_type, messages):
                self.assertEqual(source_type, 0)
                parsed = ET.fromstring(xml)
                self.assertIsNone(parsed.find(".//Print"))
                csv_path = Path(parsed.findtext(
                    "./Command/ExportPrintPreviewToImage/RecordSet/FileName"
                ))
                with csv_path.open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream, delimiter="\t"))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0], self._row())
                copied = Path(parsed.findtext(
                    "./Command/ExportPrintPreviewToImage/Format"
                ))
                self.assertTrue(copied.exists())
                (target.parent / "preview_1.png").write_bytes(b"PNG-content")
                return (
                    '<Response><Command><ExportPrintPreviewToImage/>'
                    '</Command></Response>'
                )

            app = Mock()
            app.XMLScript.side_effect = export
            app.Formats.Item.return_value = document
            self.bt.app = app

            ok = self.bt.export_gs1_btxml_preview(
                str(original), self._row(), str(target)
            )

            self.assertTrue(ok)
            self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), checksum)
            app.XMLScript.assert_called_once()
            document.Close.assert_called_once_with(1)
            app.Formats.Item.assert_called_once_with(
                str(target.parent / "btxml_preview.btw")
            )
            app.Print.assert_not_called()
            app.PrintOut.assert_not_called()

    @patch("win32com.client.VARIANT")
    def test_missing_export_png_fails_without_retry(self, variant):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "operator.btw"
            original.write_bytes(b"immutable BTW")
            target = Path(root) / "preview" / "preview_%PageNumber%.png"
            app = Mock()
            app.XMLScript.return_value = (
                '<Response><Command><ExportPrintPreviewToImage/>'
                '</Command></Response>'
            )
            self.bt.app = app
            self.assertFalse(self.bt.export_gs1_btxml_preview(
                str(original), self._row(), str(target)
            ))
            app.XMLScript.assert_called_once()
            app.Print.assert_not_called()
            app.PrintOut.assert_not_called()
            self.assertFalse((target.parent / "preview_1.png").exists())

    def test_incomplete_record_rejected_before_com(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "operator.btw"
            original.write_bytes(b"immutable BTW")
            self.bt.app = Mock()
            self.assertFalse(self.bt.export_gs1_btxml_preview(
                str(original), {"DM": "anything"},
                str(Path(root) / "preview_%PageNumber%.png")
            ))
            self.bt.app.XMLScript.assert_not_called()


if __name__ == "__main__":
    unittest.main()
