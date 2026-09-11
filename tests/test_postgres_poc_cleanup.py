import unittest
from pathlib import Path
from unittest.mock import patch

from build_tools import build_exe


ROOT = Path(__file__).resolve().parent.parent


class PostgreSqlPocCleanupTests(unittest.TestCase):
    def test_postgres_poc_backend_and_gui_hooks_are_removed(self):
        self.assertFalse((ROOT / "db.py").exists())

        gui = (ROOT / "bartender_gui.py").read_text(encoding="utf-8")
        removed_markers = (
            "from db import",
            "_db_probe_async",
            "_try_store_csv_import_db",
            "_db_print_tracking_ready",
            "_open_db_browser_window",
            "_open_db_print_history_window",
            "_last_db_import",
            "БД (PoC)",
            "История печати (PoC)",
        )
        for marker in removed_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, gui)

    def test_bartender_csv_database_helpers_are_preserved(self):
        gui = (ROOT / "bartender_gui.py").read_text(encoding="utf-8")
        for marker in (
            "def _check_template_db(",
            "def _template_db_connection_count(",
            "def _rebind_template_db(",
            "DatabaseConnections",
            "UseDatabase",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, gui)

    @patch("build_tools.build_exe.subprocess.call", return_value=0)
    @patch(
        "build_tools.build_exe._write_windows_version_file",
        return_value=Path("version_info.txt"),
    )
    def test_runtime_and_build_dependencies_do_not_include_psycopg(
        self, _version_file, call
    ):
        self.assertEqual(build_exe.main(), 0)
        command = call.call_args.args[0]
        self.assertFalse(any("psycopg" in argument.lower() for argument in command))

        for relative_path in (
            "requirements.txt",
            "build_tools/requirements.txt",
            "BarTenderGUI.spec",
        ):
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertNotIn("psycopg", content.lower())


if __name__ == "__main__":
    unittest.main()
