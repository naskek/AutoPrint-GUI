import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import licensing
from licensing import LicenseErrorCode, LicenseValidationError


class LicenseEntrypointTests(unittest.TestCase):
    @patch("licensing.show_license_error")
    @patch("licensing.load_and_validate_license")
    @patch("licensing.get_machine_hash", return_value="sha256:" + ("a" * 64))
    @patch("licensing._get_license_base_dir", return_value=Path(r"D:\Portable\AutoPrint"))
    def test_only_config_license_path_is_used(
        self,
        get_license_base_dir,
        get_machine_hash,
        validate_license,
        show_error,
    ):
        expected_payload = Mock()
        validate_license.return_value = expected_payload

        result = licensing.ensure_license_or_exit()

        self.assertIs(result, expected_payload)
        validate_license.assert_called_once_with(
            Path(r"D:\Portable\AutoPrint") / "config" / "license.json",
            "sha256:" + ("a" * 64),
        )
        show_error.assert_not_called()

    @patch("licensing.show_license_error")
    @patch("licensing.get_machine_hash")
    @patch("licensing._get_license_base_dir", return_value=Path(r"D:\Portable\AutoPrint"))
    def test_machine_id_failure_is_shown_without_a_machine_code(
        self,
        get_license_base_dir,
        get_machine_hash,
        show_error,
    ):
        error = LicenseValidationError(
            LicenseErrorCode.MACHINE_ID_UNAVAILABLE,
            "machine id unavailable",
        )
        get_machine_hash.side_effect = error

        result = licensing.ensure_license_or_exit()

        self.assertIsNone(result)
        show_error.assert_called_once_with(
            error,
            None,
            Path(r"D:\Portable\AutoPrint") / "config" / "license.json",
        )


if __name__ == "__main__":
    unittest.main()
