"""Issue #5: preserve user BTW bytes and template metadata across upgrades."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bartender_gui
import template_storage
from template_storage import (
    bootstrap_template_registry,
    import_user_template,
    migrate_legacy_templates,
    read_template_registry,
    write_template_registry,
)


class UserTemplatePersistenceTests(unittest.TestCase):
    def test_import_collision_preserves_existing_and_selects_distinct_variant(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            source = home / "incoming" / "16x16.btw"
            source.parent.mkdir()
            source.write_bytes(b"new-user-edited-template")
            user = home / "AppData" / "AutoPrintData" / "templates"
            user.mkdir(parents=True)
            old = user / "16x16.btw"
            old.write_bytes(b"existing-user-template")

            selected = import_user_template(source, user, "16x16.btw")
            self.assertEqual(old.read_bytes(), b"existing-user-template")
            self.assertEqual(selected.read_bytes(), b"new-user-edited-template")
            self.assertNotEqual(selected, old)
            self.assertIn(".import-", selected.name)
            self.assertEqual(
                import_user_template(source, user, "16x16.btw"), selected
            )
            self.assertEqual(len(list(user.glob("*.btw"))), 2)

    def test_import_same_content_and_same_source_file_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            source = directory / "sticker60x40.btw"
            source.write_bytes(b"sticker-existing")
            self.assertEqual(
                import_user_template(source, directory, source.name), source
            )
            other = directory / "incoming.btw"
            other.write_bytes(source.read_bytes())
            self.assertEqual(
                import_user_template(other, directory, source.name), source
            )
            self.assertEqual(len(list(directory.glob("*.import-*.btw"))), 0)

    def test_invalid_import_name_cannot_escape_user_data(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "input.btw"
            source.write_bytes(b"not-a-real-btw")
            destination = Path(root) / "persistent"
            for name in ("../escape.btw", r"..\\escape.btw", "not_btw.txt"):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        import_user_template(source, destination, name)
            self.assertFalse((Path(root) / "escape.btw").exists())

    def test_import_copy_failure_does_not_create_partial_user_file(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "incoming.btw"
            source.write_bytes(b"important-original")
            destination = Path(root) / "user"
            destination.mkdir()
            with patch.object(template_storage.shutil, "copyfile",
                              side_effect=OSError("read-only volume")):
                with self.assertRaises(OSError):
                    import_user_template(source, destination, "incoming.btw")
            self.assertEqual(source.read_bytes(), b"important-original")
            self.assertEqual(list(destination.iterdir()), [])

    def test_migration_preserves_all_differing_files_and_original_sources(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            old_install = home / "BarTenderGUI" / "templates"
            new_install = home / "AutoPrint" / "templates"
            persistent = home / "AutoPrintData" / "templates"
            for directory in (old_install, new_install, persistent):
                directory.mkdir(parents=True)
            (old_install / "16x16.btw").write_bytes(b"old-custom-template")
            (new_install / "16x16.btw").write_bytes(b"current-install-template")
            (persistent / "16x16.btw").write_bytes(b"existing-persistent")
            before = {
                path: (path / "16x16.btw").read_bytes()
                for path in (old_install, new_install, persistent)
            }
            for _ in range(2):
                results = migrate_legacy_templates(
                    destination_dir=persistent,
                    legacy_dirs=[old_install, new_install],
                )
                self.assertNotIn("error", [item["status"] for item in results])
            self.assertEqual(
                (persistent / "16x16.btw").read_bytes(), b"existing-persistent"
            )
            variants = list(persistent.glob("16x16.legacy-*.btw"))
            self.assertEqual(len(variants), 2)
            self.assertEqual(
                {p.read_bytes() for p in variants},
                {b"old-custom-template", b"current-install-template"},
            )
            for path, original in before.items():
                self.assertEqual((path / "16x16.btw").read_bytes(), original)

    def test_migration_read_error_leaves_existing_and_no_partials(self):
        with tempfile.TemporaryDirectory() as root:
            source_dir = Path(root) / "legacy"
            user_dir = Path(root) / "user"
            source_dir.mkdir()
            user_dir.mkdir()
            (source_dir / "30x20.btw").write_bytes(b"legacy-locked-btw")
            (user_dir / "other.btw").write_bytes(b"user-important")
            with patch.object(template_storage.shutil, "copyfile",
                              side_effect=PermissionError("locked")):
                result = migrate_legacy_templates(
                    destination_dir=user_dir, legacy_dirs=[source_dir]
                )
            self.assertEqual(result[0]["status"], "error")
            self.assertFalse((user_dir / "30x20.btw").exists())
            self.assertEqual(
                (user_dir / "other.btw").read_bytes(), b"user-important"
            )
            self.assertFalse(list(user_dir.glob(".autoprint-template-*")))

    def test_registry_survives_simulated_upgrade_uninstall_reinstall(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            persistent = home / "AutoPrintData"
            templates = persistent / "templates"
            templates.mkdir(parents=True)
            (templates / "16x16.btw").write_bytes(b"operator-custom-btw")
            (templates / "custom.btw").write_bytes(b"operator-second-btw")
            registry = persistent / "template-registry.json"
            configured = {
                "16x16": {
                    "id": "16x16", "name": "My Jar", "file_name": "16x16.btw",
                    "part_mode": "manual", "printer": "TSC TE310",
                },
                "custom": {
                    "id": "custom", "name": "Custom",
                    "file_name": "custom.btw", "part_mode": "blank",
                    "printer": "",
                },
            }
            original_config = {
                "label_templates": configured,
                "sticker_btw": r"C:\\old-install\\sticker60x40.btw",
                "show_print_dialog": True,
            }
            first = bootstrap_template_registry(
                original_config, templates, registry
            )
            self.assertEqual(first["label_templates"], configured)
            self.assertTrue(registry.is_file())
            before = (templates / "16x16.btw").read_bytes()
            # Installer files + config.json are gone; user data is not.
            second = bootstrap_template_registry({}, templates, registry)
            self.assertEqual(second["label_templates"], configured)
            self.assertEqual(
                second["sticker_btw"], str(templates / "sticker60x40.btw")
            )
            self.assertEqual((templates / "16x16.btw").read_bytes(), before)
            self.assertTrue((templates / "custom.btw").is_file())
            write_template_registry(configured, "sticker60x40.btw",
                                    path=registry)
            self.assertEqual(
                read_template_registry(registry)["label_templates"], configured
            )

    def test_corrupt_registry_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "template-registry.json"
            path.write_text("{broken-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                bootstrap_template_registry(
                    {"label_templates": {"16x16": {}}},
                    Path(root) / "templates", path,
                )
            with self.assertRaises(json.JSONDecodeError):
                write_template_registry({"16x16": {}}, path=path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken-json")

    def test_gui_template_import_updates_metadata_filename_not_old_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            source = home / "incoming.btw"
            source.write_bytes(b"new-gs1-template")
            user = home / "user"
            user.mkdir()
            previous = user / "16x16.btw"
            previous.write_bytes(b"production-original")
            app = Mock()
            app._get_label_template.return_value = {"file_name": "16x16.btw"}
            app._sanitize_template_filename.side_effect = (
                lambda file_name, _template_id: file_name
            )
            with patch.object(bartender_gui, "TEMPLATES_DIR", str(user)):
                target, file_name = bartender_gui.App._copy_template_into_workspace(
                    app, str(source), "16x16"
                )
            self.assertEqual(Path(target).read_bytes(), b"new-gs1-template")
            self.assertEqual(file_name, Path(target).name)
            self.assertNotEqual(file_name, "16x16.btw")
            self.assertEqual(previous.read_bytes(), b"production-original")

    def test_gui_sticker_import_persists_selected_version(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            source = home / "new-sticker.btw"
            source.write_bytes(b"new-sticker-asset")
            user = home / "persistent" / "templates"
            user.mkdir(parents=True)
            previous = user / "sticker60x40.btw"
            previous.write_bytes(b"old-custom-sticker")
            app = object.__new__(bartender_gui.App)
            app.cfg = {}
            app.label_templates = {}
            app.__dict__["_template_registry_path"] = (
                home / "persistent" / "template-registry.json"
            )
            with patch.object(bartender_gui, "TEMPLATES_DIR", str(user)), \
                 patch.object(bartender_gui, "save_config"):
                chosen = bartender_gui.App._import_sticker_template(
                    app, str(source)
                )
            self.assertEqual(previous.read_bytes(), b"old-custom-sticker")
            self.assertEqual(Path(chosen).read_bytes(), b"new-sticker-asset")
            self.assertNotEqual(Path(chosen), previous)
            self.assertEqual(app.cfg["sticker_btw"], chosen)
            self.assertEqual(
                read_template_registry(app._template_registry_path)["sticker_file"],
                Path(chosen).name,
            )


if __name__ == "__main__":
    unittest.main()
