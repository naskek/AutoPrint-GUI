import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            conflict_files = list((destination / "archive").glob("16x16.legacy-*.btw"))
            self.assertEqual(list(destination.glob("*.legacy-*.btw")), [])
            self.assertEqual(len(conflict_files), 1)
            self.assertEqual(conflict_files[0].read_bytes(), b"user-legacy-version")
            self.assertEqual(first[0]["status"], "conflict-preserved")
            self.assertEqual(second[0]["status"], "conflict-preserved")


    def test_existing_root_legacy_backup_is_not_copied_again(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "BarTenderGUI" / "templates"
            destination = Path(root) / "AutoPrintData" / "templates"
            legacy.mkdir(parents=True)
            destination.mkdir(parents=True)

            source = legacy / "16x16.btw"
            source.write_bytes(b"previous-custom")
            (destination / "16x16.btw").write_bytes(b"current-active")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
            previous_backup = destination / f"16x16.legacy-{digest}.btw"
            previous_backup.write_bytes(source.read_bytes())

            events = migrate_legacy_templates(
                destination_dir=destination, legacy_dirs=[legacy]
            )
            self.assertEqual(events[0]["status"], "conflict-preserved")
            self.assertEqual(Path(events[0]["target"]), previous_backup)
            self.assertEqual(previous_backup.read_bytes(), b"previous-custom")
            self.assertEqual((destination / "16x16.btw").read_bytes(), b"current-active")
            self.assertFalse((destination / "archive").exists())

    def test_fresh_collision_uses_archive_without_changing_active_template(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "BarTenderGUI" / "templates"
            destination = Path(root) / "AutoPrintData" / "templates"
            legacy.mkdir(parents=True)
            destination.mkdir(parents=True)
            (legacy / "30x20.btw").write_bytes(b"old")
            (destination / "30x20.btw").write_bytes(b"active")

            first = migrate_legacy_templates(
                destination_dir=destination, legacy_dirs=[legacy]
            )
            second = migrate_legacy_templates(
                destination_dir=destination, legacy_dirs=[legacy]
            )
            self.assertEqual(first[0]["target"], second[0]["target"])
            self.assertEqual(Path(first[0]["target"]).parent, destination / "archive")
            self.assertEqual(Path(first[0]["target"]).read_bytes(), b"old")
            self.assertEqual((destination / "30x20.btw").read_bytes(), b"active")
            self.assertFalse(list(destination.glob("*.legacy-*.btw")))
            self.assertEqual(len(list((destination / "archive").glob("*.btw"))), 1)

    def test_archive_copy_failure_keeps_active_and_source_intact(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "BarTenderGUI" / "templates"
            destination = Path(root) / "AutoPrintData" / "templates"
            legacy.mkdir(parents=True)
            destination.mkdir(parents=True)
            source = legacy / "16x16.btw"
            source.write_bytes(b"legacy-good")
            active = destination / "16x16.btw"
            active.write_bytes(b"active-good")

            with patch("template_storage.shutil.copyfile",
                       side_effect=PermissionError("source is locked")):
                events = migrate_legacy_templates(
                    destination_dir=destination, legacy_dirs=[legacy]
                )
            self.assertEqual([event["status"] for event in events], ["error"])
            self.assertEqual(source.read_bytes(), b"legacy-good")
            self.assertEqual(active.read_bytes(), b"active-good")
            self.assertEqual(list((destination / "archive").glob("*.btw")), [])
            self.assertEqual(
                list((destination / "archive").glob(".autoprint-template-*")), []
            )

if __name__ == "__main__":
    unittest.main()
