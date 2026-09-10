import base64
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing import LicenseErrorCode, LicenseValidationError, load_and_validate_license
from licensing.signature import PRODUCTION_PUBLIC_KEYS


APP_ID = "autoprint-gui"
KEY_ID = "test-key"
MACHINE_HASH = "sha256:" + ("a" * 64)
PRODUCTION_KEY_ID = "autoprint-license-ed25519-2026-09"


def _signed_document(private_key, payload=None, key_id=KEY_ID):
    payload = dict(payload or {
        "license_id": "RZ-2026-0001",
        "customer": "Customer name",
        "app_id": APP_ID,
        "issued_at": "2026-07-13T00:00:00Z",
        "expires_at": "2027-07-13T00:00:00Z",
        "machine_hash": MACHINE_HASH,
        "features": ["print"],
    })
    signed_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        "key_id": key_id,
        "payload": payload,
        "signature": base64.b64encode(private_key.sign(signed_bytes)).decode("ascii"),
    }


def _raw_public_key(private_key):
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class ProductionKeyringTests(unittest.TestCase):
    def test_current_production_public_key_is_configured(self):
        self.assertIn(PRODUCTION_KEY_ID, PRODUCTION_PUBLIC_KEYS)
        self.assertEqual(len(PRODUCTION_PUBLIC_KEYS[PRODUCTION_KEY_ID]), 32)


class LicenseValidationTests(unittest.TestCase):
    def test_valid_signed_license_is_accepted(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        payload = {
            "license_id": "RZ-2026-0001",
            "customer": "Customer name",
            "app_id": APP_ID,
            "issued_at": "2026-07-13T00:00:00Z",
            "expires_at": "2027-07-13T00:00:00Z",
            "machine_hash": MACHINE_HASH,
            "features": ["print"],
        }
        signed_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        document = {
            "key_id": KEY_ID,
            "payload": payload,
            "signature": base64.b64encode(private_key.sign(signed_bytes)).decode("ascii"),
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            license_path = Path(tmp_dir) / "license.json"
            license_path.write_text(json.dumps(document), encoding="utf-8")

            result = load_and_validate_license(
                license_path,
                MACHINE_HASH,
                now_utc=dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
                keyring={KEY_ID: public_key},
            )

        self.assertEqual(result.license_id, "RZ-2026-0001")
        self.assertEqual(result.customer, "Customer name")
        self.assertEqual(result.features, ("print",))

    def test_tampered_payload_is_rejected_as_invalid_signature(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        payload = {
            "license_id": "RZ-2026-0001",
            "customer": "Original customer",
            "app_id": APP_ID,
            "issued_at": "2026-07-13T00:00:00Z",
            "expires_at": "2027-07-13T00:00:00Z",
            "machine_hash": MACHINE_HASH,
            "features": ["print"],
        }
        signed_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        document = {
            "key_id": KEY_ID,
            "payload": payload,
            "signature": base64.b64encode(private_key.sign(signed_bytes)).decode("ascii"),
        }
        document["payload"]["customer"] = "Tampered customer"

        with tempfile.TemporaryDirectory() as tmp_dir:
            license_path = Path(tmp_dir) / "license.json"
            license_path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(LicenseValidationError) as raised:
                load_and_validate_license(
                    license_path,
                    MACHINE_HASH,
                    now_utc=dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
                    keyring={KEY_ID: public_key},
                )

        self.assertEqual(raised.exception.code, LicenseErrorCode.INVALID_SIGNATURE)

    def test_file_and_json_failures_have_stable_error_codes(self):
        private_key = Ed25519PrivateKey.generate()
        now = dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing = Path(tmp_dir) / "missing.json"
            with self.assertRaises(LicenseValidationError) as missing_error:
                load_and_validate_license(missing, MACHINE_HASH, now_utc=now, keyring={})
            self.assertEqual(missing_error.exception.code, LicenseErrorCode.FILE_NOT_FOUND)

            invalid = Path(tmp_dir) / "invalid.json"
            invalid.write_text('{"key_id":"a","key_id":"b"}', encoding="utf-8")
            with self.assertRaises(LicenseValidationError) as json_error:
                load_and_validate_license(invalid, MACHINE_HASH, now_utc=now, keyring={})
            self.assertEqual(json_error.exception.code, LicenseErrorCode.INVALID_JSON)

            malformed = Path(tmp_dir) / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            with self.assertRaises(LicenseValidationError) as malformed_error:
                load_and_validate_license(malformed, MACHINE_HASH, now_utc=now, keyring={})
            self.assertEqual(malformed_error.exception.code, LicenseErrorCode.INVALID_JSON)

    def test_unknown_key_and_invalid_base64_are_rejected(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = _raw_public_key(private_key)
        now = dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "license.json"
            path.write_text(json.dumps(_signed_document(private_key, key_id="unknown")), encoding="utf-8")
            with self.assertRaises(LicenseValidationError) as unknown_error:
                load_and_validate_license(path, MACHINE_HASH, now_utc=now, keyring={KEY_ID: public_key})
            self.assertEqual(unknown_error.exception.code, LicenseErrorCode.UNKNOWN_KEY)

            invalid_signature = _signed_document(private_key)
            invalid_signature["signature"] = "not base64!"
            path.write_text(json.dumps(invalid_signature), encoding="utf-8")
            with self.assertRaises(LicenseValidationError) as signature_error:
                load_and_validate_license(path, MACHINE_HASH, now_utc=now, keyring={KEY_ID: public_key})
            self.assertEqual(signature_error.exception.code, LicenseErrorCode.INVALID_SIGNATURE)

    def test_schema_must_contain_exact_fields_and_types(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = _raw_public_key(private_key)
        now = dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)
        payloads = []
        missing_customer = _signed_document(private_key)["payload"]
        del missing_customer["customer"]
        payloads.append(missing_customer)
        extra_field = dict(_signed_document(private_key)["payload"])
        extra_field["extra"] = True
        payloads.append(extra_field)
        invalid_features = dict(_signed_document(private_key)["payload"])
        invalid_features["features"] = ["print", "print"]
        payloads.append(invalid_features)

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "license.json"
            for payload in payloads:
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(_signed_document(private_key, payload)), encoding="utf-8")
                    with self.assertRaises(LicenseValidationError) as raised:
                        load_and_validate_license(
                            path,
                            MACHINE_HASH,
                            now_utc=now,
                            keyring={KEY_ID: public_key},
                        )
                    self.assertEqual(raised.exception.code, LicenseErrorCode.INVALID_FORMAT)

    def test_envelope_must_contain_exact_fields(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = _raw_public_key(private_key)
        now = dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)
        missing_signature = _signed_document(private_key)
        del missing_signature["signature"]
        extra_field = _signed_document(private_key)
        extra_field["extra"] = True

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "license.json"
            for document in (missing_signature, extra_field):
                with self.subTest(document=document):
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(LicenseValidationError) as raised:
                        load_and_validate_license(
                            path,
                            MACHINE_HASH,
                            now_utc=now,
                            keyring={KEY_ID: public_key},
                        )
                    self.assertEqual(raised.exception.code, LicenseErrorCode.INVALID_FORMAT)

    def test_json_formatting_and_key_order_do_not_change_signature_validity(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = _raw_public_key(private_key)
        document = _signed_document(private_key)
        reordered = {
            "signature": document["signature"],
            "payload": dict(reversed(list(document["payload"].items()))),
            "key_id": document["key_id"],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "license.json"
            path.write_text(json.dumps(reordered, indent=4, ensure_ascii=False), encoding="utf-8")

            result = load_and_validate_license(
                path,
                MACHINE_HASH,
                now_utc=dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
                keyring={KEY_ID: public_key},
            )

        self.assertEqual(result.license_id, "RZ-2026-0001")

    def test_time_app_machine_and_feature_rules_have_distinct_errors(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = _raw_public_key(private_key)
        base_payload = _signed_document(private_key)["payload"]
        cases = [
            (
                dt.datetime(2026, 7, 12, 23, 59, 59, tzinfo=dt.timezone.utc),
                base_payload,
                MACHINE_HASH,
                LicenseErrorCode.NOT_YET_VALID,
            ),
            (
                dt.datetime(2027, 7, 13, tzinfo=dt.timezone.utc),
                base_payload,
                MACHINE_HASH,
                LicenseErrorCode.EXPIRED,
            ),
            (
                dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
                {**base_payload, "app_id": "other-app"},
                MACHINE_HASH,
                LicenseErrorCode.APP_MISMATCH,
            ),
            (
                dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
                base_payload,
                "sha256:" + ("b" * 64),
                LicenseErrorCode.MACHINE_MISMATCH,
            ),
            (
                dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
                {**base_payload, "features": ["db"]},
                MACHINE_HASH,
                LicenseErrorCode.FEATURE_MISSING,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "license.json"
            for now, payload, machine_hash, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    path.write_text(json.dumps(_signed_document(private_key, payload)), encoding="utf-8")
                    with self.assertRaises(LicenseValidationError) as raised:
                        load_and_validate_license(
                            path,
                            machine_hash,
                            now_utc=now,
                            keyring={KEY_ID: public_key},
                        )
                    self.assertEqual(raised.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
