"""Mock-only tests of Issue #60's opt-in sample-data rendering experiment."""
import ast
import csv
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock

from tools.probe_preview_gs1_sample_data import (
    PREVIEW_DB_FIELDS, DB_PREFIX, set_documented_sample_values,
    validate_sample_rows,
)
from tools.gs1_dm_audit import BATCH_FIELDS


def valid_dm(n: int) -> str:
    first13 = "0460123456789"
    checksum = (10 - sum(
        int(ch) * (3 if idx % 2 == 0 else 1)
        for idx, ch in enumerate(first13)
    ) % 10) % 10
    gtin = first13 + str(checksum)
    serial = f"SERIAL{n:07d}"  # 13 printable chars
    assert len(serial) == 13
    return "01" + gtin + "21" + serial + "\x1d" + "93" + "ABCD"


class _Format:
    def __init__(self, *, db=True, gs1=True, dispid=71):
        self.UseDatabase = True
        self._oleobj_ = Mock()
        self._oleobj_.GetIDsOfNames.return_value = dispid
        self._db = db
        self._gs1 = gs1

    def UsedNames(self, kind, delim):
        if kind == 1:
            fields = PREVIEW_DB_FIELDS if self._db else ("DM",)
            return "\n".join(DB_PREFIX + field for field in fields)
        if kind == 0:
            return "\n".join(
                ("GS1_GTIN", "GS1_SERIAL", "GS1_CRYPTO")
                if self._gs1 else ("DM",)
            )
        raise AssertionError("Unexpected name type")


class SampleDataProbeTests(TestCase):
    def sample_values(self):
        return {
            "ShortName": "Хрен",
            "ShortGTIN": "67890",
            "PROD_DATE": "01.11.2026",
            "PART_NUM": "111",
            "DM": valid_dm(1),
            "NUM": "1",
        }

    def test_exact_documented_indexed_property_put_for_all_db_fields(self):
        fmt = _Format()
        values = self.sample_values()
        api = SimpleNamespace(DISPATCH_PROPERTYPUT=4)

        set_documented_sample_values(fmt, values, api)

        fmt._oleobj_.GetIDsOfNames.assert_called_once_with("UsedNamesSampleData")
        self.assertFalse(fmt.UseDatabase)
        expected = [
            ((71, 0, 4, False, 0, DB_PREFIX + field, str(values[field])),)
            for field in PREVIEW_DB_FIELDS
        ]
        actual = [tuple(call.args) for call in fmt._oleobj_.Invoke.call_args_list]
        self.assertEqual(actual, [args[0] for args in expected])

    def test_unavailable_member_fails_without_trying_other_api(self):
        fmt = _Format(dispid=None)
        with self.assertRaisesRegex(RuntimeError, "No unambiguous"):
            set_documented_sample_values(
                fmt, self.sample_values(), SimpleNamespace(DISPATCH_PROPERTYPUT=4)
            )
        self.assertTrue(fmt.UseDatabase)
        fmt._oleobj_.Invoke.assert_not_called()

    def test_missing_database_field_fails_before_any_property_put(self):
        fmt = _Format(db=False)
        with self.assertRaisesRegex(RuntimeError, "database-field contract"):
            set_documented_sample_values(
                fmt, self.sample_values(), SimpleNamespace(DISPATCH_PROPERTYPUT=4)
            )
        fmt._oleobj_.GetIDsOfNames.assert_not_called()
        fmt._oleobj_.Invoke.assert_not_called()

    def test_missing_gs1_components_fails_before_any_property_put(self):
        fmt = _Format(gs1=False)
        with self.assertRaisesRegex(RuntimeError, "GS1 component"):
            set_documented_sample_values(
                fmt, self.sample_values(), SimpleNamespace(DISPATCH_PROPERTYPUT=4)
            )
        fmt._oleobj_.GetIDsOfNames.assert_not_called()
        fmt._oleobj_.Invoke.assert_not_called()

    def test_input_requires_two_valid_distinct_01_21_93_codes(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as root:
            path = Path(root) / "sample.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=BATCH_FIELDS, delimiter="\t")
                writer.writeheader()
                for i in (1, 2):
                    writer.writerow({
                        "ShortName": "Test",
                        "ShortGTIN": "890",
                        "EXP_DATE": "02.05.2027",
                        "PROD_DATE": "01.11.2026",
                        "PART_NUM": "123",
                        "DM": valid_dm(i),
                        "NUM": str(i),
                    })
            rows = validate_sample_rows(path)
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["DM"], rows[1]["DM"])
            self.assertIn("\x1d93", rows[0]["DM"])

    def test_no_print_or_db_object_lookup_in_proof_script(self):
        path = Path(__file__).resolve().parents[1] / "tools" / "probe_preview_gs1_sample_data.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {
            "Print", "PrintOut", "GetDatabase", "SetNamedSubStringValue",
            "ExportPrintPreviewToImage", "ExportPrintPreviewRangeToImage",
        }
        calls = {
            call.func.attr
            for call in ast.walk(tree)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        }
        self.assertFalse(calls & forbidden)


if __name__ == "__main__":
    main()
