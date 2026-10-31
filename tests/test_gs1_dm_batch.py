import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import bartender_gui
from tools.gs1_dm_audit import GS, audit, parse_dm93
from tools.inspect_gs1_btw_sources import database_configuration_hints


def synthetic_dm(serial, verifier="ABCD"):
    assert len(serial) == 13
    return "01" + "04640430910775" + "21" + serial + GS + "93" + verifier


class _Databases:
    def __init__(self, *items):
        self.items = items
        self.Count = len(items)

    def GetDatabase(self, index):
        return self.items[index - 1]


class ConfigurationInspectionTests(unittest.TestCase):
    def test_read_only_xml_hints_exclude_secret_credentials(self):
        xml = (
            '<Configuration password="secret-123">'
            '<RecordSet Name="tmp_batch"/>'
            '<FileName>C:/Data/tmp_batch.csv</FileName>'
            '<Password>secret-123</Password>'
            '</Configuration>'
        )
        hints = database_configuration_hints(xml)
        self.assertIn(("name", "tmp_batch"), hints)
        self.assertIn(("filename", "C:/Data/tmp_batch.csv"), hints)
        self.assertNotIn("secret-123", repr(hints))


class GS1BatchSafetyTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(bartender_gui.App)
        self.app.logger = Mock()

    def test_parser_accepts_quoted_serial_and_preserves_separator(self):
        raw = synthetic_dm('a1"b2,c3!d4e5')
        self.assertEqual(len(raw), 38)
        self.assertEqual(
            parse_dm93(raw, "04640430910775"),
            ("04640430910775", 'a1"b2,c3!d4e5', "ABCD"),
        )
        self.assertEqual(raw[31], GS)

    def test_parser_rejects_missing_or_moved_gs_and_wrong_gtin(self):
        raw = synthetic_dm("123456789ABCD")
        with self.assertRaisesRegex(ValueError, "boundary"):
            parse_dm93(raw.replace(GS, "X", 1))
        with self.assertRaisesRegex(ValueError, "GTIN does not match"):
            parse_dm93(raw, "04640430910776")
        with self.assertRaisesRegex(ValueError, "check digit"):
            parse_dm93(raw[:15] + "6" + raw[16:])

    def test_kontur_csv_audit_preserves_multiple_rows(self):
        values = [synthetic_dm('a1"b2,c3!d4e5'), synthetic_dm("123456789ABCD", "WXYZ")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kontur.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                w = csv.writer(file, delimiter="\t")
                for value in values:
                    w.writerow([value, "04640430910775", "Synthetic test"])
            count, errors, previews = audit(path)
            self.assertEqual(count, 2)
            self.assertFalse(errors)
            self.assertEqual(len(previews), 2)

    def test_production_tmp_batch_roundtrip_preserves_original_dm(self):
        values = [synthetic_dm('a1"b2,c3!d4e5'), synthetic_dm("123456789ABCD", "WXYZ")]
        self.app.REQ_COLS = list(bartender_gui.DEFAULT_REQ_COLS)
        self.app._bool_var = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as tmp:
            self.app.cfg = {"legacy_tmp_dir": str(Path(tmp) / "not_existing")}
            path = Path(tmp) / "tmp_batch.csv"
            rows = [
                {"ShortName": "TEST", "ShortGTIN": "0775", "DM": value, "NUM": str(i)}
                for i, value in enumerate(values, start=1)
            ]
            self.app._write_tmp_batch_csv(rows, str(path), add_calib=False)
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                data = list(csv.DictReader(file, delimiter="\t"))
            self.assertEqual([r["DM"] for r in data], values)
            self.assertEqual([r["NUM"] for r in data], ["1", "2"])

    def test_failed_tmp_batch_write_never_returns_stale_path(self):
        self.app.REQ_COLS = list(bartender_gui.DEFAULT_REQ_COLS)
        self.app._bool_var = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as tmp:
            self.app.cfg = {"legacy_tmp_dir": str(Path(tmp) / "unused")}
            # Open-for-write fails because tmp is an existing directory.
            with self.assertRaisesRegex(RuntimeError, "печать остановлена"):
                self.app._write_tmp_batch_csv(
                    [{"DM": synthetic_dm("123456789ABCD")}],
                    path=tmp,
                    add_calib=False,
                )

    def test_database_dm_never_eligible_for_direct_fallback(self):
        self.assertTrue(self.app._requires_database_dm(
            {"db_fields": ["DM", "ShortName"], "named_fields": ["NUM"]}
        ))
        self.assertTrue(self.app._requires_database_dm(
            {"template_fields": ["DM"], "db_fields": []}
        ))
        self.assertFalse(self.app._requires_database_dm(
            {"db_fields": ["ShortName"], "named_fields": ["DM"]}
        ))

    def test_verified_text_database_path_required_before_dm_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "tmp_batch.csv")
            db = SimpleNamespace(
                Type=0, FileName=path,
                TextFile=SimpleNamespace(FileName=path),
            )
            fmt = SimpleNamespace(Databases=_Databases(db))
            self.assertTrue(self.app._verify_dm_batch_database(fmt, path))
            self.assertFalse(self.app._verify_dm_batch_database(
                fmt, str(Path(tmp) / "stale.csv")
            ))
            self.assertFalse(self.app._verify_dm_batch_database(
                SimpleNamespace(Databases=_Databases()), path
            ))

    def test_com_database_inspection_error_fails_closed(self):
        class BrokenDatabases:
            Count = 1

            def GetDatabase(self, index):
                raise RuntimeError("Невозможно найти базу данных 12345678.")

        fmt = SimpleNamespace(Databases=BrokenDatabases())
        self.assertFalse(self.app._verify_dm_batch_database(
            fmt, "tmp_batch.csv"
        ))


if __name__ == "__main__":
    unittest.main()
