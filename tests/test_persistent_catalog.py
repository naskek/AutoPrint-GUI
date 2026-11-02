"""Issue #6: catalog persists independently of install and preserves legacy SQLite."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from catalog_runtime import initialize_catalog_runtime
from catalog_store import CatalogStore, SCHEMA_VERSION, get_catalog_db_path
from catalog_persistence import migrate_legacy_catalog


class PersistentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.installed = self.root / "AutoPrint"
        self.legacy = self.installed / "data" / "catalog.sqlite3"
        self.profile = self.root / "AutoPrintData"
        self.target = self.profile / "data" / "catalog.sqlite3"

    def _legacy_db(self, path=None, name="Operator catalog"):
        path = path or self.legacy
        store = CatalogStore(path)
        store.initialize()
        store.create_product({"gtin": "04601234567890", "name": name,
                              "print_template": "16x16", "short_name": "Short"})
        return store

    def _start(self, paths=None):
        return initialize_catalog_runtime(
            self.installed, database_path=self.target,
            legacy_database_paths=[self.legacy] if paths is None else paths
        )

    def test_default_catalog_location_uses_isolated_persistent_profile(self):
        with patch.dict(os.environ, {"AUTOPRINT_USER_DATA_DIR": str(self.profile)}):
            self.assertEqual(get_catalog_db_path(), self.target)
        self.assertEqual(get_catalog_db_path(self.installed), self.legacy)

    def test_migrate_old_database_and_keep_recoverable_backup(self):
        original = self._legacy_db()
        original_product = original.list_products()[0]
        result = self._start()
        self.assertTrue(result.ready, result.error)
        self.assertEqual(result.migration_source, self.legacy)
        self.assertEqual(result.snapshot.products[0], original_product)
        self.assertEqual(original.list_products()[0], original_product)
        self.assertTrue(self.legacy.is_file())
        backups = list((self.target.parent / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(CatalogStore(backups[0]).list_products()[0], original_product)
        with sqlite3.connect(self.target) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

        again = self._start()
        self.assertTrue(again.ready, again.error)
        self.assertIsNone(again.migration_source)
        self.assertEqual(len(list((self.target.parent / "backups").glob("*.sqlite3"))), 1)

    def test_existing_persistent_database_wins_over_legacy_and_seed(self):
        self._legacy_db(name="Legacy")
        target_store = CatalogStore(self.target)
        target_store.initialize()
        target_store.create_product({"gtin": "999", "name": "Persistent"})
        result = self._start()
        self.assertTrue(result.ready, result.error)
        self.assertEqual([p.name for p in result.snapshot.products], ["Persistent"])
        self.assertEqual(CatalogStore(self.legacy).list_products()[0].name, "Legacy")

    def test_conflicting_legacy_versions_fail_closed(self):
        self._legacy_db()
        alternate = self.root / "BarTenderGUI" / "data" / "catalog.sqlite3"
        self._legacy_db(path=alternate, name="Other")
        result = self._start([self.legacy, alternate])
        self.assertFalse(result.ready)
        self.assertIn("Conflicting", result.error)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.legacy.exists())
        self.assertTrue(alternate.exists())

    def test_corrupted_source_does_not_fall_back_to_empty_or_excel(self):
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_bytes(b"corrupt live SQLite")
        result = self._start()
        self.assertFalse(result.ready)
        self.assertEqual(self.legacy.read_bytes(), b"corrupt live SQLite")
        self.assertFalse(self.target.exists())

    def test_unknown_schema_fails_closed(self):
        self.legacy.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.legacy)) as db:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
            db.commit()
        result = self._start()
        self.assertFalse(result.ready)
        self.assertIn("version", result.error)
        self.assertFalse(self.target.exists())

    def test_migration_failure_does_not_publish_partial_authoritative_db(self):
        self._legacy_db()
        with patch("catalog_persistence.os.link", side_effect=OSError("disk full")):
            result = self._start()
        self.assertFalse(result.ready)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.legacy.is_file())
        self.assertEqual(len(list(self.target.parent.glob("*.tmp"))), 0)

    def test_fresh_install_creates_only_persistent_catalog(self):
        result = self._start([])
        self.assertTrue(result.ready, result.error)
        self.assertEqual(result.snapshot.products, ())
        self.assertTrue(self.target.is_file())
        self.assertFalse(self.legacy.exists())

    def test_backup_is_independent_after_live_catalog_mutation(self):
        self._legacy_db()
        self.assertTrue(self._start().ready)
        backup = next((self.target.parent / "backups").glob("*.sqlite3"))
        CatalogStore(self.target).create_product({"gtin": "98765", "name": "New"})
        self.assertEqual(len(CatalogStore(backup).list_products()), 1)
        self.assertEqual(len(CatalogStore(self.target).list_products()), 2)


if __name__ == "__main__":
    unittest.main()
