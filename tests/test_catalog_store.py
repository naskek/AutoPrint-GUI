import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from catalog_store import (
    SCHEMA_VERSION,
    CatalogConflictError,
    CatalogSchemaError,
    CatalogStore,
    CatalogValidationError,
    get_catalog_db_path,
)


class CatalogStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "data" / "catalog.sqlite3"
        self.store = CatalogStore(self.db_path)
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_path_is_under_application_data_directory(self):
        self.assertEqual(
            get_catalog_db_path(self.root),
            self.root / "data" / "catalog.sqlite3",
        )

    def test_initialize_creates_versioned_schema_and_is_idempotent(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertEqual(version, SCHEMA_VERSION)
        self.assertTrue(
            {"products", "product_print_settings", "catalog_meta"}.issubset(tables)
        )
        self.store.create_product({"gtin": "0460123", "name": "Product"})

        CatalogStore(self.db_path).initialize()

        self.assertEqual(len(self.store.list_products()), 1)

    def test_crud_normalizes_gtin_updates_revision_and_cascades(self):
        product = self.store.create_product(
            {
                "gtin": " 0460-123 ",
                "name": "Product",
                "print_template": "16x16",
            }
        )
        self.assertEqual(product.gtin, "0460123")
        self.assertEqual(self.store.read_snapshot().revision, 1)

        updated = self.store.update_product(
            product.id,
            {"name": "Updated", "gtin": "0460 124"},
            expected_revision=1,
        )
        self.assertEqual(updated.gtin, "0460124")
        self.assertEqual(updated.name, "Updated")
        self.assertEqual(self.store.read_snapshot().revision, 2)

        unchanged = self.store.update_product(
            product.id, {"name": "Updated"}, expected_revision=2
        )
        self.assertEqual(unchanged.updated_at, updated.updated_at)
        self.assertEqual(self.store.read_snapshot().revision, 2)

        with self.assertRaises(CatalogConflictError):
            self.store.update_product(product.id, {"name": "Stale"}, expected_revision=1)

        self.store.delete_product(product.id, expected_revision=2)
        self.assertEqual(self.store.list_products(), [])
        with closing(sqlite3.connect(self.db_path)) as connection:
            settings_count = connection.execute(
                "SELECT COUNT(*) FROM product_print_settings"
            ).fetchone()[0]
        self.assertEqual(settings_count, 0)
        self.assertEqual(self.store.read_snapshot().revision, 3)

    def test_unique_gtin_is_enforced_after_normalization(self):
        self.store.create_product({"gtin": "0460-123"})
        with self.assertRaises(CatalogValidationError):
            self.store.create_product({"gtin": "0460123"})

    def test_apply_import_is_last_row_wins_and_merge_is_idempotent(self):
        result = self.store.apply_import(
            [
                {"gtin": "123-45", "name": "First", "print_template": "16x16"},
                {"gtin": "12345", "name": "Last", "print_template": "30x20"},
            ],
            present_fields={"gtin", "name", "print_template"},
            has_short_col=True,
        )
        self.assertEqual((result.added, result.updated, result.unchanged), (1, 0, 0))
        self.assertEqual(result.duplicate_count, 1)
        snapshot = self.store.read_snapshot()
        self.assertEqual(snapshot.products[0].name, "Last")
        self.assertEqual(snapshot.products[0].print_template, "30x20")
        self.assertTrue(snapshot.has_short_col)

        again = self.store.apply_import(
            [{"gtin": "12345", "name": "Last", "print_template": "30x20"}],
            present_fields={"gtin", "name", "print_template"},
            has_short_col=True,
            expected_revision=snapshot.revision,
        )
        self.assertEqual((again.added, again.updated, again.unchanged), (0, 0, 1))
        self.assertEqual(self.store.read_snapshot().revision, snapshot.revision)

    def test_import_preview_counts_changes_without_writing(self):
        self.store.create_product({"gtin": "111", "name": "Existing", "brand": "Keep"})
        revision = self.store.read_snapshot().revision

        preview = self.store.preview_import(
            [
                {"gtin": "111", "name": "Changed"},
                {"gtin": "222", "name": "Added"},
            ],
            present_fields={"gtin", "name"},
            expected_revision=revision,
        )

        self.assertEqual((preview.added, preview.updated, preview.unchanged), (1, 1, 0))
        snapshot = self.store.read_snapshot()
        self.assertEqual(snapshot.revision, revision)
        self.assertEqual(snapshot.products[0].name, "Existing")

    def test_import_absent_fields_are_preserved_and_present_empty_is_cleared(self):
        product = self.store.create_product(
            {"gtin": "111", "name": "Old", "brand": "Keep", "client": "Clear"}
        )
        result = self.store.apply_import(
            [{"gtin": "111", "name": "New", "client": ""}],
            present_fields={"gtin", "name", "client"},
            expected_revision=1,
        )

        updated = self.store.get_product(product.id)
        self.assertEqual(result.updated, 1)
        self.assertEqual(updated.name, "New")
        self.assertEqual(updated.brand, "Keep")
        self.assertEqual(updated.client, "")

    def test_invalid_import_is_rejected_before_any_write(self):
        with self.assertRaises(CatalogValidationError):
            self.store.apply_import(
                [
                    {"gtin": "123", "name": "Valid"},
                    {"gtin": "not-a-gtin", "name": "Invalid"},
                ]
            )
        self.assertEqual(self.store.read_snapshot().products, ())
        self.assertEqual(self.store.read_snapshot().revision, 0)

    def test_database_failure_rolls_back_entire_import(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_broken_product
                BEFORE INSERT ON products
                WHEN NEW.name = 'boom'
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic failure');
                END
                """
            )

        with self.assertRaises(sqlite3.DatabaseError):
            self.store.apply_import(
                [
                    {"gtin": "111", "name": "ok"},
                    {"gtin": "222", "name": "boom"},
                ]
            )

        self.assertEqual(self.store.read_snapshot().products, ())
        self.assertEqual(self.store.read_snapshot().revision, 0)

    def test_backup_is_a_valid_independent_database(self):
        self.store.create_product({"gtin": "123", "name": "Backup product"})

        backup_path = self.store.backup()

        self.assertTrue(backup_path.is_file())
        backup_store = CatalogStore(backup_path)
        backup_store.initialize()
        self.assertEqual(backup_store.list_products()[0].name, "Backup product")

    def test_failed_required_backup_prevents_mutation(self):
        with patch.object(self.store, "backup", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.create_product({"gtin": "123", "name": "Not written"})

        self.assertEqual(self.store.read_snapshot().products, ())
        self.assertEqual(self.store.read_snapshot().revision, 0)

    def test_unknown_newer_schema_is_rejected(self):
        other_path = self.root / "future.sqlite3"
        with closing(sqlite3.connect(other_path)) as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

        with self.assertRaises(CatalogSchemaError):
            CatalogStore(other_path).initialize()

    def test_corrupt_database_is_not_recreated(self):
        corrupt_path = self.root / "corrupt.sqlite3"
        corrupt_path.write_bytes(b"not a sqlite database")

        with self.assertRaises(CatalogSchemaError):
            CatalogStore(corrupt_path).initialize()

        self.assertEqual(corrupt_path.read_bytes(), b"not a sqlite database")

    def test_template_usage_is_counted_in_sqlite(self):
        self.store.create_product({"gtin": "111", "print_template": "16x16"})
        self.store.create_product({"gtin": "222", "print_template": "16x16"})
        self.store.create_product({"gtin": "333", "print_template": "30x20"})

        self.assertEqual(self.store.count_template_usage("16x16"), 2)


if __name__ == "__main__":
    unittest.main()
