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
            count, valid, invalid, calibration, errors, previews = audit(path)
            self.assertEqual((count, valid, invalid, calibration), (2, 2, 0, 0))
            self.assertFalse(errors)
            self.assertEqual(len(previews), 2)

    @staticmethod
    def _write_batch_fixture(path, calibration_rows=6, extra_dm=None):
        fields = list(bartender_gui.DEFAULT_REQ_COLS)
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            dummy = {
                name: ("1" if name == "NUM" else "000" if name == "ShortGTIN" else "X")
                for name in fields
            }
            for _ in range(calibration_rows):
                writer.writerow(dummy)
            for idx, dm in enumerate(
                [synthetic_dm('a1"b2,c3!d4e5'), synthetic_dm("123456789ABCD", "WXYZ")]
                + ([extra_dm] if extra_dm is not None else []),
                start=1,
            ):
                writer.writerow({"ShortName": "TEST", "ShortGTIN": "0775", "DM": dm, "NUM": str(idx)})

    def test_exact_six_calibration_rows_are_reported_but_do_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self._write_batch_fixture(path)
            total, valid, invalid, calibration, errors, previews = audit(path)
            self.assertEqual((total, valid, invalid, calibration), (8, 2, 0, 6))
            self.assertFalse(errors)
            self.assertEqual([line for line, _ in previews], [8, 9])

    def test_five_calibration_rows_are_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self._write_batch_fixture(path, calibration_rows=5)
            total, valid, invalid, calibration, errors, _ = audit(path)
            self.assertEqual((total, valid, calibration), (7, 2, 5))
            self.assertGreater(invalid, 0)
            self.assertTrue(any("incomplete calibration" in reason for _, reason in errors))

    def test_invalid_real_dm_is_not_treated_as_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self._write_batch_fixture(path, extra_dm="X")
            total, valid, invalid, calibration, errors, _ = audit(path)
            self.assertEqual((total, valid, invalid, calibration), (9, 2, 1, 6))
            self.assertTrue(any(line == 10 for line, _ in errors))

    def test_duplicate_real_dm_is_rejected_without_leaking_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            raw = synthetic_dm("123456789ABCD")
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=list(bartender_gui.DEFAULT_REQ_COLS),
                    delimiter="\t"
                )
                writer.writeheader()
                writer.writerow({"DM": raw, "NUM": "1"})
                writer.writerow({"DM": raw, "NUM": "2"})
            total, valid, invalid, calibration, errors, _ = audit(path)
            self.assertEqual((total, valid, invalid, calibration), (2, 1, 1, 0))
            self.assertEqual(errors, [(3, "duplicate DM (first seen at line 2)")])
            self.assertNotIn(raw, repr(errors))

    def test_seventh_calibration_row_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self._write_batch_fixture(path, calibration_rows=7)
            _, valid, invalid, calibration, errors, _ = audit(path)
            self.assertEqual((valid, invalid, calibration), (2, 1, 6))
            self.assertTrue(any("unexpected calibration" in reason for _, reason in errors))

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

    def _configured_format(self, config, *, refs="tmp_batch.tmp_batch.DM"):
        class BrokenGetDatabase:
            Count = 1
            Configuration = config

            def GetDatabase(self, index):
                raise RuntimeError("Невозможно найти базу данных 12345678.")

        return SimpleNamespace(
            Databases=BrokenGetDatabase(),
            UsedNames=lambda kind, sep: refs if kind == 1 else "",
        )

    def test_production_barcode_binding_works_without_getdatabase(self):
        # The real BarTender 2022 R5 installation printed ten labels with
        # precisely this COM capability shape: Count and Configuration work,
        # while GetDatabase(1) and GetDatabase('tmp_batch') both fail.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self._write_batch_fixture(path, calibration_rows=0)
            config = (
                '<Configuration><Database Name="@1"/>'
                '<RecordSet Name="tmp_batch"/>'
                '<Field Name="[tmp_batch].[tmp_batch].[DM]"/>'
                '</Configuration>'
            )
            fmt = self._configured_format(config)
            self.assertTrue(self.app._verify_dm_batch_database(fmt, str(path)))
            self.app.logger.log.assert_called_once()
            self.assertIn("PDF smoke", self.app.logger.log.call_args.args[0])

    def test_explicit_config_path_must_match_current_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self._write_batch_fixture(path, calibration_rows=0)
            config_ok = (
                '<Configuration><RecordSet Name="tmp_batch"/>'
                f'<FileName>{path}</FileName></Configuration>'
            )
            self.assertTrue(self.app._verify_dm_batch_database(
                self._configured_format(config_ok), str(path)
            ))
            config_stale = (
                '<Configuration><RecordSet Name="tmp_batch"/>'
                f'<FileName>{Path(tmp) / "stale.csv"}</FileName></Configuration>'
            )
            self.assertFalse(self.app._verify_dm_batch_database(
                self._configured_format(config_stale), str(path)
            ))

    def test_gs1_batch_blocks_missing_or_wrong_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self._write_batch_fixture(path, calibration_rows=0)
            fmt = self._configured_format(
                '<Configuration><RecordSet Name="tmp_batch"/></Configuration>',
                refs="another_db.other.DM",
            )
            self.assertFalse(self.app._verify_dm_batch_database(fmt, str(path)))
            fmt = self._configured_format(
                '<Configuration><RecordSet Name="other"/></Configuration>'
            )
            self.assertFalse(self.app._verify_dm_batch_database(fmt, str(path)))
            fmt = self._configured_format("<not-valid")
            self.assertFalse(self.app._verify_dm_batch_database(fmt, str(path)))

            # Missing file and malformed header must not submit a job.
            fmt = self._configured_format(
                '<Configuration><RecordSet Name="tmp_batch"/></Configuration>'
            )
            self.assertFalse(self.app._verify_dm_batch_database(
                fmt, str(Path(tmp) / "missing.csv")
            ))
            path.write_text("ShortName\tNUM\nA\t1\n", encoding="utf-8-sig")
            self.assertFalse(self.app._verify_dm_batch_database(fmt, str(path)))

    def test_gs1_named_sources_prevent_direct_fallback(self):
        for name in ("GS1_GTIN", "GS1_SERIAL", "GS1_CRYPTO"):
            self.assertTrue(self.app._requires_database_dm({
                "named_fields": [name],
                "db_fields": [],
                "template_fields": [],
            }))
        self.assertFalse(self.app._requires_database_dm({
            "named_fields": ["ShortName", "PART_NUM", "DM"],
            "db_fields": [],
            "template_fields": [],
        }))


if __name__ == "__main__":
    unittest.main()
