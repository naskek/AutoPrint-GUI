"""No-COM regression tests for Issue #60's read-only capability inspector."""
import ast
from pathlib import Path
import unittest

from tools.inspect_preview_gs1_capabilities import (
    connection_hints,
    inspect_contract,
    member_signatures,
    split_used_names,
)


class _TypeInfo:
    def GetTypeAttr(self):
        attr = [None] * 7
        attr[6] = 3
        return attr

    def GetFuncDesc(self, index):
        vals = [
            (10, 4, 3),  # UsedNamesSampleData, indexed PROPERTYPUT with value
            (11, 2, 2),  # UsedNamesSampleData, indexed PROPERTYGET
            (12, 1, 11), # ExportPrintPreviewRangeToImage, METHOD
        ]
        dispid, kind, nargs = vals[index]
        desc = [None] * 7
        desc[0] = dispid
        desc[4] = kind
        desc[6] = nargs
        return desc

    def GetNames(self, memid):
        return {
            10: ("UsedNamesSampleData",),
            11: ("UsedNamesSampleData",),
            12: ("ExportPrintPreviewRangeToImage",),
        }[memid]


class PreviewGs1CapabilitiesTests(unittest.TestCase):
    def test_detects_actual_database_dm_and_named_gs1_inputs(self):
        names = split_used_names(
            "tmp_batch.tmp_batch.DM\n tmp_batch.tmp_batch.ShortName\n"
        )
        named = {"DM", "GS1_GTIN", "GS1_SERIAL", "GS1_CRYPTO", "PART_NUM"}
        contract = inspect_contract(names, named)
        self.assertTrue(contract["db_dm_used"])
        self.assertEqual(contract["gs1_named_components"],
                         ["GS1_CRYPTO", "GS1_GTIN", "GS1_SERIAL"])
        self.assertTrue(contract["raw_dm_named"])

    def test_wrong_db_binding_is_not_claimed_as_pass(self):
        contract = inspect_contract(
            {"tmp_batch.other.DM", "some_table.DM"},
            {"DM"}
        )
        self.assertFalse(contract["db_dm_used"])
        self.assertEqual(contract["gs1_named_components"], [])

    def test_typeinfo_reports_signatures_without_calling_com_members(self):
        info = member_signatures(_TypeInfo())
        self.assertEqual(info["UsedNamesSampleData"], [("put", 3), ("get", 2)])
        self.assertEqual(info["ExportPrintPreviewRangeToImage"], [("method", 11)])

    def test_database_xml_logs_names_but_never_credentials_or_paths(self):
        xml = (
            '<DatabaseSetup Password="secret" FileName="C:/prod/secrets.csv">'
            '<DatabaseConnection Name="@1" Password="dontlog"/>'
            '<RecordSet Name="tmp_batch" Connection="@1"/>'
            '</DatabaseSetup>'
        )
        self.assertEqual(connection_hints(xml),
                         [("databaseconnection", "@1"), ("recordset", "tmp_batch")])

    def test_no_print_export_or_db_mutation_calls(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "tools" / "inspect_preview_gs1_capabilities.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {
            "Print", "PrintOut", "ExportToFile",
            "ExportPrintPreviewRangeToImage", "ExportPrintPreviewToImage",
            "GetDatabase", "SetNamedSubStringValue", "UsedNamesSampleData",
        }
        called = {
            call.func.attr
            for call in ast.walk(tree)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        }
        self.assertFalse(forbidden & called, forbidden & called)


if __name__ == "__main__":
    unittest.main()
