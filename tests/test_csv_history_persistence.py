"""Issue #7: durable CSV history migration and truthful submit/unknown state."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bartender_gui
from csv_history_store import (
    CsvHistoryError, CsvHistoryStore, get_csv_history_path, legacy_config_paths,
)


class CsvHistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.database = self.data / "csv-history.sqlite3"
        self.store = CsvHistoryStore(self.database)
        self.csv = self.root / "batch-001.csv"

    def _config(self, name, stats):
        path = self.root / name / "config" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"csv_print_stats": stats}), encoding="utf-8")
        return path

    def test_migrate_two_legacy_configs_and_keep_all_history(self):
        first = {
            str(self.csv): {
                "printed": True, "print_runs": 1, "last_print": "2026-10-01 11:00:00",
                "history": [{"ts": "2026-10-01 11:00:00", "sent": 10}],
            },
        }
        second = {
            str(self.csv): {
                "printed": True, "print_runs": 2, "last_print": "2026-10-02 11:00:00",
                "history": [
                    {"ts": "2026-10-01 11:00:00", "sent": 10},
                    {"ts": "2026-10-02 11:00:00", "sent": 20},
                ],
            },
        }
        old_path = self._config("BarTenderGUI", first)
        new_path = self._config("AutoPrint", second)
        self.store.initialize([old_path, new_path])
        saved = self.store.read_all()[str(self.csv)]
        self.assertEqual(saved["state"], "submitted_to_bartender")
        self.assertEqual(len(saved["history"]), 2)
        self.assertEqual(saved["print_runs"], 2)
        self.assertEqual(saved["last_print"], "2026-10-02 11:00:00")
        self.assertTrue(old_path.is_file())
        self.assertTrue(new_path.is_file())

        old_path.unlink()
        new_path.unlink()
        self.store.initialize([old_path, new_path])
        self.assertEqual(self.store.read_all()[str(self.csv)]["history"], saved["history"])

    def test_events_are_atomic_and_never_claim_physical_completion(self):
        self.store.initialize()
        self.store.append_event(
            self.csv, "submitted_to_bartender", printer="TEST", fmt="16x16",
            sent=10, timestamp="2026-11-02 10:00:00",
        )
        first = self.store.read_all()[str(self.csv)]
        self.assertTrue(first["printed"])
        self.assertEqual(first["state"], "submitted_to_bartender")
        self.assertEqual(first["print_runs"], 1)
        self.store.append_event(
            self.csv, "unknown", printer="TEST", timestamp="2026-11-02 10:02:00",
        )
        recovered = CsvHistoryStore(self.database).read_all()[str(self.csv)]
        self.assertEqual(recovered["state"], "unknown")
        self.assertEqual(recovered["print_runs"], 1)
        self.assertEqual(recovered["last_print"], "2026-11-02 10:00:00")
        self.assertEqual(len(recovered["history"]), 2)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM print_events").fetchone()[0], 2)

    def test_existing_persistent_db_is_authoritative_not_reimported(self):
        self.store.initialize()
        self.store.append_event(self.csv, "failed", timestamp="2026-11-02 12:00:00")
        config = self._config(
            "old", {str(self.csv): {"printed": True, "print_runs": 500}},
        )
        self.store.initialize([config])
        self.assertEqual(self.store.read_all()[str(self.csv)]["state"], "failed")

    def test_invalid_existing_db_is_not_replaced(self):
        self.data.mkdir(parents=True)
        self.database.write_bytes(b"invalid sqlite")
        with self.assertRaises(CsvHistoryError):
            self.store.initialize([])
        self.assertEqual(self.database.read_bytes(), b"invalid sqlite")

    def test_unreadable_legacy_config_fails_closed(self):
        legacy = self.root / "old.json"
        legacy.write_text("{broken", encoding="utf-8")
        with self.assertRaises(CsvHistoryError):
            self.store.initialize([legacy])
        self.assertFalse(self.database.exists())

    def test_unsupported_schema_is_not_mutated(self):
        self.data.mkdir(parents=True)
        with sqlite3.connect(self.database) as db:
            db.execute("PRAGMA user_version = 99")
        with self.assertRaises(CsvHistoryError):
            self.store.initialize()
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_path_identity_is_absolute_path_not_basename(self):
        self.store.initialize()
        left = self.root / "first" / "same.csv"
        right = self.root / "second" / "same.csv"
        self.store.append_event(left, "submitted_to_bartender")
        self.store.append_event(right, "failed")
        data = self.store.read_all()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[str(left)]["state"], "submitted_to_bartender")
        self.assertEqual(data[str(right)]["state"], "failed")

    def test_isolation_environment_and_legacy_discovery(self):
        with patch.dict(os.environ, {
            "AUTOPRINT_USER_DATA_DIR": str(self.data),
            "LOCALAPPDATA": str(self.root),
        }):
            self.assertEqual(get_csv_history_path(), self.data / "csv-history.sqlite3")
            paths = legacy_config_paths(self.root / "AutoPrint")
            self.assertIn(self.root / "BarTenderGUI" / "config" / "config.json", paths)
            self.assertIn(self.root / "AutoPrint" / "config" / "config.json", paths)

    def test_gui_legacy_mark_printed_persists_submitted_only(self):
        self.store.initialize()
        app = object.__new__(bartender_gui.App)
        app._csv_history_store = self.store
        app.csv_stats = {}
        app.logger = Mock()
        app._render_csv_list = Mock()
        bartender_gui.App._mark_csv_printed(
            app, str(self.csv), 3, printer="TEST", fmt="16x16"
        )
        saved = CsvHistoryStore(self.database).read_all()[str(self.csv)]
        self.assertEqual(saved["state"], "submitted_to_bartender")
        self.assertEqual(saved["history"][0]["sent"], 3)
        self.assertNotEqual(saved["state"], "completed")
        app._render_csv_list.assert_called_once()


if __name__ == "__main__":
    unittest.main()
