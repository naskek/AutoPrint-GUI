"""Issue #83: one GS1 BTXML Print command and safe submission semantics.

All calls use a fake XMLScript object. Never interact with real BarTender,
the printer, the spooler or production marking codes in CI.
"""
import csv
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from bartender_com import BT


class BtxmlBatchContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user_profile = str(self.root / "profile")
        self.env = patch.dict(os.environ, {"AUTOPRINT_USER_DATA_DIR": self.user_profile})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.logger = SimpleNamespace(log=Mock(), err=Mock())
        self.bt = BT(self.logger)
        self.btw = self.root / "test.btw"
        self.btw.write_bytes(b"synthetic harmless BTW stand-in")
        self.csv_path = self.root / "tmp_batch.csv"
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=("ShortName", "ShortGTIN", "EXP_DATE",
                                   "PROD_DATE", "PART_NUM", "DM", "NUM"),
                delimiter="\t",
            )
            writer.writeheader()
            for number in (1, 2):
                writer.writerow({
                    "ShortName": f"TEST-{number}",
                    "ShortGTIN": "893",
                    "EXP_DATE": "01.01.2030", "PROD_DATE": "01.01.2029",
                    "PART_NUM": "SYNTH",
                    "DM": f"SAFE_SYNTHETIC_{number}",
                    "NUM": number,
                })
        pythoncom = ModuleType("pythoncom")
        pythoncom.VT_BYREF = 0x4000
        pythoncom.VT_DISPATCH = 9
        win32com = ModuleType("win32com")
        win32com.__path__ = []
        client = ModuleType("win32com.client")
        client.VARIANT = lambda code, value: SimpleNamespace(typecode=code, value=value)
        self.com_patch = patch.dict(sys.modules, {
            "pythoncom": pythoncom,
            "win32com": win32com,
            "win32com.client": client,
        })
        self.com_patch.start()
        self.addCleanup(self.com_patch.stop)

    @staticmethod
    def sent_response():
        return ('<Response Version="2.0"><Command Name="GS1Batch">'
                '<Print JobLastStatus="Sent" JobCompleted="true">'
                '<Message Severity="Information" ID="1606"/>'
                '</Print></Command></Response>')

    def _submit(self, *, show_dialog=False):
        return self.bt.submit_gs1_btxml_batch(
            str(self.btw), str(self.csv_path),
            "Synthetic No Printer", "AutoPrint TEST", 2,
            expected_dm=("SAFE_SYNTHETIC_1", "SAFE_SYNTHETIC_2"),
            copies=1, show_dialog=show_dialog,
        )

    def test_print_xml_has_explicit_path_range_printer_and_no_retry(self):
        xml = BT.build_gs1_btxml_print_request(
            "C:/temp/test.btw", "C:/temp/tmp_batch.csv",
            "Printer Name", "Synthetic job", 6000,
        )
        root = ET.fromstring(xml)
        self.assertEqual(len(root.findall(".//Print")), 1)
        self.assertEqual(len(root.findall(".//RecordSet")), 1)
        self.assertEqual(root.find(".//RecordSet").get("Name"), "tmp_batch")
        self.assertEqual(root.find(".//RecordSet").get("AddIfNone"), "false")
        self.assertEqual(root.findtext(".//RecordSet/FileName"), "C:/temp/tmp_batch.csv")
        self.assertEqual(root.findtext(".//RecordSet/Delimitation"), "btDelimTab")
        self.assertEqual(root.findtext(".//PrintSetup/RecordRange"), "1-6000")
        self.assertEqual(root.findtext(".//PrintSetup/Printer"), "Printer Name")
        self.assertEqual(root.findtext(".//PrintSetup/UseDatabase"), "true")
        self.assertEqual(root.findtext(".//PrintSetup/AutoPrintAgain"), "false")
        self.assertEqual(root.findtext(".//PrintSetup/IdenticalCopiesOfLabel"), "1")
        fmt = root.find(".//Print/Format")
        self.assertEqual(fmt.get("SaveAtEndOfJob"), "false")
        self.assertEqual(fmt.get("CloseAtEndOfJob"), "true")
        self.assertEqual(root.find(".//Print").get("WaitForJobToComplete"), "false")
        self.assertEqual(root.find(".//Print").get("ReturnPrintData"), "false")
        self.assertIsNone(root.find(".//PrintOut"))
        self.assertEqual(len(root.findall(".//Command")), 1)

    def test_invalid_xml_arguments_do_not_make_request(self):
        for kw in ({"copies": 2}, {"row_count": 0}, {"printer": ""}):
            args = dict(template_path="t.btw", csv_path="tmp_batch.csv",
                        printer="Queue", job_name="Job", row_count=1, copies=1)
            args.update(kw)
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):
                    BT.build_gs1_btxml_print_request(**args)

    def test_positive_response_requires_sent_and_no_error(self):
        self.assertEqual(BT._btxml_batch_print_response(self.sent_response()), "submitted")
        for response in (
            "", None, "<Bad>", '<Response><Command/></Response>',
            '<Response><Command><Print JobLastStatus="Queued"/></Command></Response>',
            '<Response><Command><Print JobLastStatus="Sent"/>'
            '<Print JobLastStatus="Sent"/></Command></Response>',
            '<Response><Command><Print JobLastStatus="Sent">'
            '<Message Severity="Error"/></Print></Command></Response>',
            '<Response><Command><Print JobLastStatus="Sent"/>'
            '<Error/></Command></Response>',
        ):
            with self.subTest(response=response):
                self.assertEqual(BT._btxml_batch_print_response(response), "ambiguous")

    def test_one_realistic_btxml_submit_with_fake_com_and_retained_files(self):
        self.bt.app = SimpleNamespace(XMLScript=Mock(return_value=self.sent_response()))
        expected_btw = self.btw.read_bytes()
        expected_csv = self.csv_path.read_bytes()
        self.assertEqual(self._submit(), "submitted")
        self.bt.app.XMLScript.assert_called_once()
        args = self.bt.app.XMLScript.call_args.args
        self.assertEqual(args[1], 0)
        root = ET.fromstring(args[0])
        path = Path(root.findtext(".//RecordSet/FileName"))
        template = Path(root.findtext(".//Print/Format"))
        self.assertTrue(path.is_file())
        self.assertTrue(template.is_file())
        self.assertNotEqual(path, self.csv_path)
        self.assertEqual(path.read_bytes(), expected_csv)
        self.assertEqual(template.read_bytes(), expected_btw)
        self.assertEqual(self.btw.read_bytes(), expected_btw)
        self.assertEqual(self.csv_path.read_bytes(), expected_csv)

    def test_exception_after_submit_stays_ambiguous_no_retries(self):
        self.bt.app = SimpleNamespace(XMLScript=Mock(side_effect=RuntimeError("lost response")))
        self.assertEqual(self._submit(), "ambiguous")
        self.bt.app.XMLScript.assert_called_once()

    def test_malformed_response_stays_ambiguous_no_retries(self):
        self.bt.app = SimpleNamespace(XMLScript=Mock(return_value="<Response/>"))
        self.assertEqual(self._submit(), "ambiguous")
        self.bt.app.XMLScript.assert_called_once()

    def test_changed_synthetic_csv_codes_are_failed_before_submit(self):
        self.bt.app = SimpleNamespace(XMLScript=Mock(return_value=self.sent_response()))
        self.csv_path.write_text(
            "ShortName\\tDM\\tNUM\\nTEST\\tDIFFERENT\\t1\\n"
            "TEST\\tDIFFERENT2\\t2\\n",
            encoding="utf-8-sig",
        )
        self.assertEqual(self._submit(), "failed")
        self.bt.app.XMLScript.assert_not_called()

    def test_dialog_or_bad_csv_is_failed_before_any_submit(self):
        self.bt.app = SimpleNamespace(XMLScript=Mock(return_value=self.sent_response()))
        self.assertEqual(self._submit(show_dialog=True), "failed")
        self.bt.app.XMLScript.assert_not_called()
        self.csv_path.unlink()
        self.assertEqual(self._submit(), "failed")
        self.bt.app.XMLScript.assert_not_called()


if __name__ == "__main__":
    unittest.main()
