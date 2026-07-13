import unittest
from unittest.mock import patch

from licensing import LicenseErrorCode, LicenseValidationError, get_machine_hash


class MachineIdTests(unittest.TestCase):
    @patch("licensing.machine_id.winreg.QueryValueEx")
    @patch("licensing.machine_id.winreg.OpenKey")
    def test_machine_guid_is_normalized_and_hashed(self, open_key, query_value):
        open_key.return_value.__enter__.return_value = object()
        query_value.return_value = (" {00112233-4455  -6677-8899-AABBCCDDEEFF} ", 1)

        result = get_machine_hash()

        self.assertEqual(
            result,
            "sha256:3779fa906e2e04a8e31a356d916c9e22f836be0ebd66deaf8d354ff286ec7834",
        )

    @patch("licensing.machine_id.winreg.QueryValueEx")
    @patch("licensing.machine_id.winreg.OpenKey")
    def test_invalid_machine_guid_is_reported_without_leaking_raw_value(self, open_key, query_value):
        open_key.return_value.__enter__.return_value = object()
        query_value.return_value = ("secret-but-invalid", 1)

        with self.assertRaises(LicenseValidationError) as raised:
            get_machine_hash()

        self.assertEqual(raised.exception.code, LicenseErrorCode.MACHINE_ID_UNAVAILABLE)
        self.assertNotIn("secret-but-invalid", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
