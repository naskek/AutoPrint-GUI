import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from build_tools import build_exe
from utils import load_config, save_config


ROOT = Path(__file__).resolve().parent.parent


class PackagingTests(unittest.TestCase):
    @patch("build_tools.build_exe.subprocess.call", return_value=0)
    @patch(
        "build_tools.build_exe._write_windows_version_file",
        return_value=Path("version_info.txt"),
    )
    def test_pyinstaller_command_includes_icon_and_bundled_templates(self, _version_file, call):
        self.assertEqual(build_exe.main(), 0)

        command = call.call_args.args[0]
        self.assertEqual(command[command.index("--name") + 1], "AutoPrint")
        icon = ROOT / "assets" / "icons" / "autoprint.ico"
        self.assertEqual(command[command.index("--icon") + 1], str(icon))
        add_data = [
            command[index + 1]
            for index, item in enumerate(command[:-1])
            if item == "--add-data"
        ]
        self.assertIn(f"{icon}{os.pathsep}assets/icons", add_data)
        for template_name in build_exe.REQUIRED_BUNDLED_TEMPLATES:
            template_path = ROOT / "templates" / template_name
            self.assertIn(
                f"{template_path}{os.pathsep}bundled_templates",
                add_data,
            )
        for operational_name in ("product_catalog.xlsx", "tmp_batch.csv", "config.json"):
            self.assertFalse(any(operational_name in item for item in command))

    def test_required_bundled_templates_are_versioned(self):
        for template_name in build_exe.REQUIRED_BUNDLED_TEMPLATES:
            with self.subTest(template_name=template_name):
                path = ROOT / "templates" / template_name
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 100)

    @patch("build_tools.build_exe.subprocess.call", return_value=0)
    @patch(
        "build_tools.build_exe._write_windows_version_file",
        return_value=Path("version_info.txt"),
    )
    def test_generated_spec_is_kept_out_of_repository_root(self, _version_file, call):
        tracked_spec = ROOT / "AutoPrint.spec"
        original_contents = tracked_spec.read_bytes()

        self.assertEqual(build_exe.main(), 0)

        command = call.call_args.args[0]
        spec_dir = Path(command[command.index("--specpath") + 1])
        self.assertEqual(spec_dir, ROOT / "build" / "pyinstaller-spec")
        self.assertNotEqual(spec_dir, ROOT)
        self.assertEqual(tracked_spec.read_bytes(), original_contents)

    def test_windows_icon_contains_required_sizes(self):
        icon_path = ROOT / "assets" / "icons" / "autoprint.ico"
        source_path = ROOT / "assets" / "icons" / "autoprint.png"

        self.assertTrue(source_path.is_file())
        with Image.open(icon_path) as icon:
            self.assertTrue(
                {(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)}
                .issubset(icon.info["sizes"])
            )

    def test_spec_uses_autoprint_icon_for_bundle_and_executable(self):
        spec = (ROOT / "AutoPrint.spec").read_text(encoding="utf-8")

        self.assertIn("ROOT = Path(__file__).resolve().parent", spec)
        self.assertNotRegex(spec, r"['\"][A-Za-z]:[/\\]")
        self.assertIn("datas.append((str(icon_file), 'assets/icons'))", spec)
        self.assertIn("datas.append((str(template_path), 'bundled_templates'))", spec)
        self.assertIn("icon=str(icon_file)", spec)

    def test_installer_files_section_contains_only_application_bundle(self):
        installer = (ROOT / "build_tools" / "installer.iss").read_text(encoding="utf-8")
        files_section = installer.split("[Files]", 1)[1].split("[Icons]", 1)[0]
        sources = [
            line.strip()
            for line in files_section.splitlines()
            if line.strip().startswith("Source:")
        ]

        self.assertEqual(
            sources,
            [
                'Source: "..\\dist\\AutoPrint\\*"; DestDir: "{app}"; '
                "Flags: ignoreversion recursesubdirs createallsubdirs"
            ],
        )

    def test_installer_preserves_upgrade_identity_and_cleans_old_shell_artifacts(self):
        installer = (ROOT / "build_tools" / "installer.iss").read_text(encoding="utf-8")

        self.assertIn("AppId={{B9F5C8A4-4F63-4B43-B93B-FA2C1E25B111}}", installer)
        self.assertIn('#define MyAppName "AutoPrint"', installer)
        self.assertIn('#define MyAppExeName "AutoPrint.exe"', installer)
        self.assertIn("DefaultDirName={localappdata}\\{#MyAppName}", installer)
        self.assertIn("SetupIconFile=..\\assets\\icons\\autoprint.ico", installer)
        self.assertIn("UninstallDisplayIcon={app}\\{#MyAppExeName}", installer)
        self.assertEqual(installer.count('IconFilename: "{app}\\{#MyAppExeName}"'), 2)
        self.assertIn("DeleteFile(ExpandConstant('{app}\\BarTenderGUI.exe'))", installer)
        self.assertIn("DeleteFile(ExpandConstant('{group}\\BarTender GUI.lnk'))", installer)

    def test_windows_version_resource_uses_autoprint_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            version_file = build_exe._write_windows_version_file(
                Path(temp_dir), "3.2.1"
            )
            contents = version_file.read_text(encoding="utf-8")

        for expected in (
            "StringStruct('CompanyName', 'AutoPrint')",
            "StringStruct('FileDescription', 'AutoPrint')",
            "StringStruct('InternalName', 'AutoPrint')",
            "StringStruct('OriginalFilename', 'AutoPrint.exe')",
            "StringStruct('ProductName', 'AutoPrint')",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, contents)

    def test_missing_config_loads_empty_and_can_be_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("utils.get_base_dir", return_value=temp_dir):
                self.assertEqual(load_config(), {})
                save_config({"show_print_dialog": False})
                self.assertTrue((Path(temp_dir) / "config" / "config.json").is_file())


if __name__ == "__main__":
    unittest.main()
