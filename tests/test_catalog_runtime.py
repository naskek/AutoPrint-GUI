import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openpyxl import Workbook

import bartender_gui
from catalog_runtime import initialize_catalog_runtime
from catalog_store import CatalogProduct, CatalogSnapshot, CatalogStore


class CatalogRuntimeStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "data" / "catalog.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _workbook(self, name, rows, headers=("GTIN", "Наименование")):
        path = self.root / name
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        workbook.close()
        return path

    def test_existing_database_wins_over_primary_excel(self):
        store = CatalogStore(self.db_path)
        store.initialize()
        store.create_product({"gtin": "111", "name": "SQLite"})
        self._workbook("product_catalog.xlsx", [("222", "Excel")])

        result = initialize_catalog_runtime(self.root)

        self.assertTrue(result.ready)
        self.assertEqual([item.gtin for item in result.snapshot.products], ["111"])
        self.assertIsNone(result.migration_source)

    def test_existing_empty_database_still_wins_over_excel(self):
        store = CatalogStore(self.db_path)
        store.initialize()
        self._workbook("product_catalog.xlsx", [("222", "Excel")])

        result = initialize_catalog_runtime(self.root)

        self.assertTrue(result.ready)
        self.assertEqual(result.snapshot.products, ())
        self.assertEqual(result.snapshot.revision, 0)

    def test_primary_excel_is_migrated_atomically(self):
        source = self._workbook(
            "product_catalog.xlsx",
            [("0460-123", "First"), ("0460123", "Last")],
        )
        original = source.read_bytes()

        result = initialize_catalog_runtime(self.root)

        self.assertTrue(result.ready)
        self.assertEqual(result.migration_source, source)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.snapshot.products[0].name, "Last")
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(self.db_path.is_file())

    def test_legacy_excel_is_used_only_without_primary(self):
        legacy = self._workbook("Список товаров.xlsx", [("123", "Legacy")])

        result = initialize_catalog_runtime(self.root)

        self.assertTrue(result.ready)
        self.assertEqual(result.migration_source, legacy)
        self.assertEqual(result.snapshot.products[0].name, "Legacy")

    def test_no_excel_creates_empty_ready_database(self):
        result = initialize_catalog_runtime(self.root)

        self.assertTrue(result.ready)
        self.assertEqual(result.snapshot.products, ())
        self.assertTrue(self.db_path.is_file())

    def test_invalid_primary_does_not_fall_back_or_leave_database(self):
        primary = self._workbook("product_catalog.xlsx", [("bad", "Broken")])
        self._workbook("Список товаров.xlsx", [("123", "Legacy")])

        result = initialize_catalog_runtime(self.root)

        self.assertFalse(result.ready)
        self.assertIn(str(primary), result.error)
        self.assertFalse(self.db_path.exists())

    def test_failed_import_does_not_leave_authoritative_database(self):
        self._workbook("product_catalog.xlsx", [("123", "Product")])

        with patch("catalog_runtime.apply_prepared_import", side_effect=OSError("disk full")):
            result = initialize_catalog_runtime(self.root)

        self.assertFalse(result.ready)
        self.assertFalse(self.db_path.exists())

    def test_corrupt_existing_database_is_preserved_without_excel_fallback(self):
        self.db_path.parent.mkdir(parents=True)
        self.db_path.write_bytes(b"not sqlite")
        self._workbook("product_catalog.xlsx", [("123", "Excel")])

        result = initialize_catalog_runtime(self.root)

        self.assertFalse(result.ready)
        self.assertEqual(self.db_path.read_bytes(), b"not sqlite")


class CatalogRuntimeGuiContractTests(unittest.TestCase):
    def _app(self):
        app = object.__new__(bartender_gui.App)
        app.catalog_store = Mock()
        app.catalog_state = "ready"
        app.catalog_error = ""
        app.catalog_revision = 1
        app._catalog_print_active = 0
        app.product_map = {"old": {}}
        app.catalog_status_label = Mock()
        app.preview_btn = Mock()
        app.print_btn = Mock()
        app.sticker_reprint_btn = Mock()
        app.bt = None
        app.logger = Mock()
        app._update_brand_hint = Mock()
        return app

    @patch("bartender_gui.build_product_map", return_value={"new": {}})
    def test_snapshot_replaces_product_map_only_after_successful_read(self, adapter):
        app = self._app()
        snapshot = Mock(revision=2, products=(), has_short_col=False)
        app.catalog_store.read_snapshot.return_value = snapshot

        app._refresh_catalog_snapshot()

        self.assertEqual(app.product_map, {"new": {}})
        self.assertEqual(app.catalog_revision, 2)

    def test_failed_refresh_keeps_previous_product_map(self):
        app = self._app()
        original = app.product_map
        app.catalog_store.read_snapshot.side_effect = OSError("read failed")

        with self.assertRaises(OSError):
            app._refresh_catalog_snapshot()

        self.assertIs(app.product_map, original)
        self.assertEqual(app.catalog_state, "error")

    def test_report_catalog_map_reads_sqlite_snapshot(self):
        app = self._app()
        app._require_catalog_ready = Mock(return_value=True)
        product = CatalogProduct(
            id="product-id",
            gtin="0460123",
            tnved="0123456789",
            permit_document="Declaration",
            permit_from="2026-01-02",
        )
        app.catalog_store.read_snapshot.return_value = CatalogSnapshot(
            products=(product,), revision=1, has_short_col=True
        )

        mapping = app._build_report_catalog_map()

        self.assertEqual(mapping["0460123"]["ТН ВЭД"], "0123456789")
        self.assertEqual(
            mapping["0460123"]["Разрешительная документация"], "Declaration"
        )


if __name__ == "__main__":
    unittest.main()
