import tempfile
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
    def test_normal_configured_license_still_works_unchanged(
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
    @patch("licensing.load_and_validate_license")
    @patch("licensing.get_machine_hash", return_value="sha256:" + ("a" * 64))
    def test_missing_default_then_valid_selected_license_allows_startup_without_copy(
        self,
        get_machine_hash,
        validate_license,
        show_error,
    ):
        missing_error = LicenseValidationError(
            LicenseErrorCode.FILE_NOT_FOUND, "missing"
        )
        expected_payload = Mock()
        validate_license.side_effect = [missing_error, expected_payload]

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "app"
            default_path = base_dir / "config" / "license.json"
            selected_path = Path(temp_dir) / "external" / "customer-license.json"
            selected_path.parent.mkdir()
            selected_path.write_text("selected-license", encoding="utf-8")

            def select_file(error, machine_hash, license_path, validate_selected):
                self.assertIs(error, missing_error)
                self.assertEqual(license_path, default_path)
                return validate_selected(selected_path, machine_hash)

            show_error.side_effect = select_file
            with patch("licensing._get_license_base_dir", return_value=base_dir):
                result = licensing.ensure_license_or_exit()

            self.assertIs(result, expected_payload)
            self.assertEqual(
                validate_license.call_args_list,
                [
                    unittest.mock.call(default_path, "sha256:" + ("a" * 64)),
                    unittest.mock.call(selected_path, "sha256:" + ("a" * 64)),
                ],
            )
            self.assertFalse(default_path.exists())
            self.assertEqual(
                selected_path.read_text(encoding="utf-8"), "selected-license"
            )

    @patch("licensing.show_license_error")
    @patch("licensing.load_and_validate_license")
    @patch("licensing.get_machine_hash", return_value="sha256:" + ("a" * 64))
    @patch("licensing._get_license_base_dir", return_value=Path(r"D:\Portable\AutoPrint"))
    def test_invalid_selected_license_keeps_startup_blocked(
        self,
        get_license_base_dir,
        get_machine_hash,
        validate_license,
        show_error,
    ):
        missing_error = LicenseValidationError(
            LicenseErrorCode.FILE_NOT_FOUND, "missing"
        )
        invalid_error = LicenseValidationError(
            LicenseErrorCode.INVALID_SIGNATURE, "invalid signature"
        )
        selected_path = Path(r"D:\Licenses\invalid.json")
        validate_license.side_effect = [missing_error, invalid_error]

        def select_invalid(_error, machine_hash, _license_path, validate_selected):
            try:
                validate_selected(selected_path, machine_hash)
            except LicenseValidationError:
                return None
            self.fail("Invalid selected license was accepted")

        show_error.side_effect = select_invalid

        result = licensing.ensure_license_or_exit()

        self.assertIsNone(result)
        self.assertEqual(validate_license.call_count, 2)
        validate_license.assert_any_call(selected_path, "sha256:" + ("a" * 64))

    @patch("licensing.show_license_error")
    @patch("licensing.load_and_validate_license")
    @patch("licensing.get_machine_hash")
    @patch("licensing._get_license_base_dir", return_value=Path(r"D:\Portable\AutoPrint"))
    def test_machine_id_failure_is_shown_without_a_machine_code(
        self,
        get_license_base_dir,
        get_machine_hash,
        validate_license,
        show_error,
    ):
        error = LicenseValidationError(
            LicenseErrorCode.MACHINE_ID_UNAVAILABLE,
            "machine id unavailable",
        )
        get_machine_hash.side_effect = error

        result = licensing.ensure_license_or_exit()

        self.assertIsNone(result)
        validate_license.assert_not_called()
        show_error.assert_called_once_with(
            error,
            None,
            Path(r"D:\Portable\AutoPrint") / "config" / "license.json",
            validate_license,
        )


if __name__ == "__main__":
    unittest.main()
