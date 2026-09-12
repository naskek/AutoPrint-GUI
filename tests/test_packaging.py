import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_tools import build_exe
from utils import load_config, save_config


ROOT = Path(__file__).resolve().parent.parent


class PackagingTests(unittest.TestCase):
    @patch("build_tools.build_exe.subprocess.call", return_value=0)
    @patch(
        "build_tools.build_exe._write_windows_version_file",
        return_value=Path("version_info.txt"),
    )
    def test_pyinstaller_command_excludes_operational_data(self, _version_file, call):
        self.assertEqual(build_exe.main(), 0)

        command = call.call_args.args[0]
        self.assertNotIn("--add-data", command)
        self.assertEqual(command[command.index("--name") + 1], "AutoPrint")

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
