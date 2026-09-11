import tempfile
import unittest
import datetime as dt
from pathlib import Path

from openpyxl import Workbook

from catalog_compat import build_product_map
from catalog_excel import (
    CATALOG_HEADERS,
    CatalogImportValidationError,
    apply_prepared_import,
    export_catalog_xlsx,
    prepare_excel_import,
    select_automatic_migration_source,
)
from catalog_store import CatalogStore
from data_processing import enrich_row, read_product_map


class CatalogExcelAndCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _workbook(self, name, headers, rows):
        path = self.root / name
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        workbook.close()
        return path

    def test_parser_ignores_blank_rows_and_uses_last_duplicate(self):
        path = self._workbook(
            "catalog.xlsx",
            ["GTIN", "Наименование", "Шаблон печати"],
            [
                ["0460-123", "First", "16x16"],
                [None, None, None],
                ["0460123", "Last", "30x20"],
            ],
        )

        prepared = prepare_excel_import(path)

        self.assertTrue(prepared.valid)
        self.assertEqual(prepared.blank_rows, 1)
        self.assertEqual(len(prepared.records), 1)
        self.assertEqual(prepared.records[0]["name"], "Last")
        self.assertEqual(prepared.records[0]["print_template"], "30x20")
        self.assertEqual(prepared.duplicates[0].gtin, "0460123")
        self.assertEqual(prepared.duplicates[0].row_numbers, (2, 4))

    def test_nonempty_invalid_row_invalidates_entire_preparation(self):
        path = self._workbook(
            "invalid.xlsx",
            ["GTIN", "Наименование"],
            [["123", "Valid"], ["not-a-gtin", "Invalid"], [None, "Missing"]],
        )

        prepared = prepare_excel_import(path)

        self.assertFalse(prepared.valid)
        self.assertEqual({error.row_number for error in prepared.errors}, {3, 4})
        store = CatalogStore(self.root / "data" / "catalog.sqlite3")
        store.initialize()
        with self.assertRaises(CatalogImportValidationError):
            apply_prepared_import(store, prepared)
        self.assertEqual(store.read_snapshot().products, ())

    def test_conflicting_gtin_headers_are_rejected(self):
        path = self._workbook("ambiguous.xlsx", ["GTIN", "ГТИН"], [["123", "123"]])

        prepared = prepare_excel_import(path)

        self.assertFalse(prepared.valid)
        self.assertTrue(any(error.code == "ambiguous_header" for error in prepared.errors))

    def test_canonical_and_fallback_weight_headers_are_ambiguous(self):
        path = self._workbook(
            "ambiguous-weight.xlsx",
            ["GTIN", "Вес", "Вес, г"],
            [["123", "10", "10"]],
        )

        prepared = prepare_excel_import(path)

        self.assertFalse(prepared.valid)
        self.assertTrue(any(error.code == "ambiguous_header" for error in prepared.errors))

    def test_unknown_columns_are_reported_but_do_not_block_import(self):
        path = self._workbook(
            "extra.xlsx", ["GTIN", "Наименование", "Extra"], [["123", "Product", "x"]]
        )

        prepared = prepare_excel_import(path)

        self.assertTrue(prepared.valid)
        self.assertEqual(prepared.unknown_headers, ("Extra",))

    def test_export_round_trip_preserves_all_catalog_fields(self):
        store = CatalogStore(self.root / "data" / "catalog.sqlite3")
        store.initialize()
        values = {
            "gtin": "04601234567890",
            "name": "Product",
            "brand": "Brand",
            "weight": "250 г",
            "shelf_life": "12 месяцев",
            "permit_document": "Declaration",
            "permit_from": "2026-01-02",
            "permit_to": "2027-01-02",
            "packaging": "Банка",
            "material": "Plastic",
            "marking_method": "Sticker",
            "tnved": "0123456789",
            "short_name": "Short",
            "print_template": "16x16",
            "client": "Client",
            "display_packaging": "Jar",
        }
        store.create_product(values)
        export_path = self.root / "export.xlsx"

        export_catalog_xlsx(store.read_snapshot().products, export_path)
        prepared = prepare_excel_import(export_path)

        self.assertTrue(prepared.valid)
        self.assertEqual(prepared.present_fields, frozenset(values))
        self.assertEqual(dict(prepared.records[0]), values)
        self.assertEqual(CATALOG_HEADERS[0], "GTIN")
        self.assertNotIn("Шаблон партии", CATALOG_HEADERS)

    def test_catalog_headers_match_existing_editor_contract(self):
        from bartender_gui import CATALOG_HEADERS as EXISTING_CATALOG_HEADERS

        self.assertEqual(list(CATALOG_HEADERS), EXISTING_CATALOG_HEADERS)

    def test_automatic_source_selection_never_replays_excel_for_existing_db(self):
        primary = self._workbook("product_catalog.xlsx", ["GTIN"], [["123"]])
        db_path = self.root / "data" / "catalog.sqlite3"

        self.assertEqual(
            select_automatic_migration_source(self.root, db_path), primary
        )
        db_path.parent.mkdir(parents=True)
        db_path.touch()
        self.assertIsNone(select_automatic_migration_source(self.root, db_path))

    def test_legacy_source_is_used_only_when_primary_is_absent(self):
        legacy = self._workbook("Список товаров.xlsx", ["GTIN"], [["123"]])

        self.assertEqual(
            select_automatic_migration_source(
                self.root, self.root / "data" / "catalog.sqlite3"
            ),
            legacy,
        )

    def test_compatibility_adapter_matches_legacy_excel_mapping(self):
        headers = [
            "ГТИН",
            "Наименование",
            "УПАКОВКА",
            "СРОК ГОДНОСТИ",
            "Партия",
            "Template",
            "Короткое имя",
            "Brand",
            "Client",
            "Упаковка для стикера",
            "Weight",
        ]
        path = self._workbook(
            "compat.xlsx",
            headers,
            [
                ["0460-123", "First", "Ведро", "2 года", "A-{DATE}", "", "Short 1", "Brand 1", "Client 1", "Pail", "1 кг"],
                ["999", "Second", "Банка", "3 недели", "B-{DATE}", "  custom   label  ", "", "Brand 2", "", "", "250 г"],
                ["0460123", "Last", "Туба", "12 месяцев", "C-{DATE}", "30x20", "Short 2", "Brand 3", "Client 3", "Tube", "500 г"],
            ],
        )
        prepared = prepare_excel_import(path)
        self.assertTrue(prepared.valid, prepared.errors)
        store = CatalogStore(self.root / "data" / "catalog.sqlite3")
        store.initialize()
        apply_prepared_import(store, prepared)
        snapshot = store.read_snapshot()

        new_mapping = build_product_map(
            snapshot.products, has_short_col=snapshot.has_short_col
        )
        legacy_mapping = read_product_map(str(path))

        self.assertEqual(new_mapping, legacy_mapping)
        self.assertNotIn("PART_TEMPLATE", new_mapping["0460123"])
        self.assertIn("Партия", prepared.unknown_headers)
        base_row = {"DM": "code", "GTIN": "0460123", "NAME": "CSV name"}
        arguments = (base_row, 1, dt.date(2026, 9, 11), None)
        self.assertEqual(
            enrich_row(*arguments, legacy_mapping, "Auto"),
            enrich_row(*arguments, new_mapping, "Auto"),
        )

    def test_enrichment_ignores_legacy_batch_template(self):
        enriched = enrich_row(
            {"DM": "code", "GTIN": "123", "NAME": "Product"},
            1,
            dt.date(2026, 9, 11),
            None,
            {
                "123": {
                    "TEMPLATE_ID": "16x16",
                    "PART_TEMPLATE": "LOT-{DATE}",
                }
            },
            "Auto",
        )

        self.assertEqual(enriched["PART_NUM"], "")


if __name__ == "__main__":
    unittest.main()
