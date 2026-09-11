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
                'Source: "..\\dist\\BarTenderGUI\\*"; DestDir: "{app}"; '
                "Flags: ignoreversion recursesubdirs createallsubdirs"
            ],
        )

    def test_missing_config_loads_empty_and_can_be_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("utils.get_base_dir", return_value=temp_dir):
                self.assertEqual(load_config(), {})
                save_config({"show_print_dialog": False})
                self.assertTrue((Path(temp_dir) / "config" / "config.json").is_file())


if __name__ == "__main__":
    unittest.main()
