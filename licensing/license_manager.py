from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature

from .signature import PRODUCTION_PUBLIC_KEYS, verify_payload_signature


APP_ID = "autoprint-gui"
REQUIRED_FEATURE = "print"
_ENVELOPE_FIELDS = {"key_id", "payload", "signature"}
_PAYLOAD_FIELDS = {
    "license_id",
    "customer",
    "app_id",
    "issued_at",
    "expires_at",
    "machine_hash",
    "features",
}
_MACHINE_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FEATURE_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class LicenseErrorCode(str, Enum):
    FILE_NOT_FOUND = "file_not_found"
    INVALID_JSON = "invalid_json"
    INVALID_FORMAT = "invalid_format"
    UNKNOWN_KEY = "unknown_key"
    INVALID_SIGNATURE = "invalid_signature"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"
    APP_MISMATCH = "app_mismatch"
    MACHINE_MISMATCH = "machine_mismatch"
    FEATURE_MISSING = "feature_missing"
    MACHINE_ID_UNAVAILABLE = "machine_id_unavailable"


class LicenseValidationError(Exception):
    def __init__(self, code: LicenseErrorCode, message: str):
        super().__init__(message)
        self.code = code


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True)
class LicensePayload:
    license_id: str
    customer: str
    app_id: str
    issued_at: dt.datetime
    expires_at: dt.datetime
    machine_hash: str
    features: tuple[str, ...]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_document(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise LicenseValidationError(
            LicenseErrorCode.FILE_NOT_FOUND,
            "License file was not found.",
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_JSON,
            "License file cannot be read as UTF-8 JSON.",
        ) from exc
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_JSON,
            "License file contains invalid or ambiguous JSON.",
        ) from exc
    if not isinstance(document, dict):
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_FORMAT,
            "License envelope must be a JSON object.",
        )
    return document


def _require_exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_FORMAT,
            f"{label} must contain exactly the required fields.",
        )


def _parse_utc(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_FORMAT,
            f"{field} must be a UTC timestamp in YYYY-MM-DDTHH:MM:SSZ format.",
        )
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_FORMAT,
            f"{field} is not a valid timestamp.",
        ) from exc


def _validated_payload(payload: dict[str, Any]) -> LicensePayload:
    _require_exact_fields(payload, _PAYLOAD_FIELDS, "License payload")
    license_id = payload["license_id"]
    customer = payload["customer"]
    app_id = payload["app_id"]
    machine_hash = payload["machine_hash"]
    features = payload["features"]
    if not isinstance(license_id, str) or not 1 <= len(license_id.strip()) <= 128:
        raise LicenseValidationError(LicenseErrorCode.INVALID_FORMAT, "license_id is invalid.")
    if not isinstance(customer, str) or not 1 <= len(customer.strip()) <= 256:
        raise LicenseValidationError(LicenseErrorCode.INVALID_FORMAT, "customer is invalid.")
    if not isinstance(app_id, str) or not app_id:
        raise LicenseValidationError(LicenseErrorCode.INVALID_FORMAT, "app_id is invalid.")
    if not isinstance(machine_hash, str) or not _MACHINE_HASH_RE.fullmatch(machine_hash):
        raise LicenseValidationError(LicenseErrorCode.INVALID_FORMAT, "machine_hash is invalid.")
    if (
        not isinstance(features, list)
        or not features
        or any(not isinstance(item, str) or not _FEATURE_RE.fullmatch(item) for item in features)
        or len(set(features)) != len(features)
    ):
        raise LicenseValidationError(LicenseErrorCode.INVALID_FORMAT, "features are invalid.")
    issued_at = _parse_utc(payload["issued_at"], "issued_at")
    expires_at = _parse_utc(payload["expires_at"], "expires_at")
    if issued_at >= expires_at:
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_FORMAT,
            "issued_at must be earlier than expires_at.",
        )
    return LicensePayload(
        license_id=license_id.strip(),
        customer=customer.strip(),
        app_id=app_id,
        issued_at=issued_at,
        expires_at=expires_at,
        machine_hash=machine_hash,
        features=tuple(features),
    )


def load_and_validate_license(
    path: str | Path,
    machine_hash: str,
    now_utc: dt.datetime | None = None,
    keyring: Mapping[str, bytes] | None = None,
) -> LicensePayload:
    document = _read_document(Path(path))
    _require_exact_fields(document, _ENVELOPE_FIELDS, "License envelope")
    key_id = document["key_id"]
    payload = document["payload"]
    signature = document["signature"]
    if not isinstance(key_id, str) or not 1 <= len(key_id) <= 128:
        raise LicenseValidationError(LicenseErrorCode.INVALID_FORMAT, "key_id is invalid.")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise LicenseValidationError(LicenseErrorCode.INVALID_FORMAT, "License envelope is invalid.")

    trusted_keys = PRODUCTION_PUBLIC_KEYS if keyring is None else keyring
    if key_id not in trusted_keys:
        raise LicenseValidationError(
            LicenseErrorCode.UNKNOWN_KEY,
            "License key_id is not trusted by this application.",
        )
    try:
        verify_payload_signature(payload, signature, key_id, trusted_keys)
    except (InvalidSignature, ValueError) as exc:
        raise LicenseValidationError(
            LicenseErrorCode.INVALID_SIGNATURE,
            "License signature is invalid.",
        ) from exc

    result = _validated_payload(payload)
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    now = now.astimezone(dt.timezone.utc)
    if now < result.issued_at:
        raise LicenseValidationError(LicenseErrorCode.NOT_YET_VALID, "License is not valid yet.")
    if now >= result.expires_at:
        raise LicenseValidationError(LicenseErrorCode.EXPIRED, "License has expired.")
    if result.app_id != APP_ID:
        raise LicenseValidationError(LicenseErrorCode.APP_MISMATCH, "License app_id does not match.")
    if result.machine_hash != machine_hash:
        raise LicenseValidationError(
            LicenseErrorCode.MACHINE_MISMATCH,
            "License is assigned to a different machine.",
        )
    if REQUIRED_FEATURE not in result.features:
        raise LicenseValidationError(
            LicenseErrorCode.FEATURE_MISSING,
            "License does not include the required print feature.",
        )
    return result
