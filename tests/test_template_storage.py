import tempfile
import unittest
from pathlib import Path

from template_storage import (
    get_bundled_templates_dir,
    get_user_data_dir,
    get_user_templates_dir,
    legacy_template_dirs,
    migrate_legacy_templates,
)


class TemplateStorageTests(unittest.TestCase):
    def test_user_templates_live_in_separate_persistent_directory(self):
        with tempfile.TemporaryDirectory() as root:
            local_appdata = Path(root) / "LocalAppData"

            data_dir = Path(get_user_data_dir(local_appdata))
            templates_dir = Path(get_user_templates_dir(local_appdata))

            self.assertEqual(data_dir, local_appdata / "AutoPrintData")
            self.assertEqual(templates_dir, data_dir / "templates")

    def test_bundled_templates_use_source_dir_in_dev_and_bundle_root_when_frozen(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "source"
            bundle = Path(root) / "_internal"

            self.assertEqual(
                Path(get_bundled_templates_dir(base, frozen=False)),
                base / "templates",
            )
            self.assertEqual(
                Path(
                    get_bundled_templates_dir(
                        base,
                        frozen=True,
                        bundle_root=bundle,
                    )
                ),
                bundle / "bundled_templates",
            )

    def test_legacy_locations_include_autoprint_and_bartendergui(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "CustomInstall"
            local = Path(root) / "LocalAppData"
            locations = legacy_template_dirs(base_dir=base, local_appdata=local)

            self.assertIn(base / "templates", locations)
            self.assertIn(base, locations)
            self.assertIn(local / "AutoPrint" / "templates", locations)
            self.assertIn(local / "BarTenderGUI" / "templates", locations)

    def test_migration_copies_legacy_template_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "legacy"
            destination = Path(root) / "data" / "templates"
            legacy.mkdir()
            source = legacy / "16x16.btw"
            source.write_bytes(b"legacy-template")

            events = migrate_legacy_templates(
                destination_dir=destination,
                legacy_dirs=[legacy],
            )

            self.assertEqual((destination / source.name).read_bytes(), b"legacy-template")
            self.assertTrue(source.is_file())
            self.assertEqual(events[0]["status"], "copied")

    def test_conflict_is_preserved_without_overwrite_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "legacy"
            destination = Path(root) / "data" / "templates"
            legacy.mkdir(parents=True)
            destination.mkdir(parents=True)

            source = legacy / "16x16.btw"
            target = destination / "16x16.btw"
            source.write_bytes(b"user-legacy-version")
            target.write_bytes(b"existing-version")

            first = migrate_legacy_templates(
                destination_dir=destination,
                legacy_dirs=[legacy],
            )
            second = migrate_legacy_templates(
                destination_dir=destination,
                legacy_dirs=[legacy],
            )

            self.assertEqual(target.read_bytes(), b"existing-version")
            conflict_files = list(destination.glob("16x16.legacy-*.btw"))
            self.assertEqual(len(conflict_files), 1)
            self.assertEqual(conflict_files[0].read_bytes(), b"user-legacy-version")
            self.assertEqual(first[0]["status"], "conflict-preserved")
            self.assertEqual(second[0]["status"], "conflict-preserved")


if __name__ == "__main__":
    unittest.main()
