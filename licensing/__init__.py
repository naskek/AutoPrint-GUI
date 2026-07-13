from pathlib import Path
import sys

from .dialogs import show_license_error
from .license_manager import (
    LicenseErrorCode,
    LicensePayload,
    LicenseValidationError,
    load_and_validate_license,
)
from .machine_id import get_machine_hash


def _get_license_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ensure_license_or_exit() -> LicensePayload | None:
    license_path = _get_license_base_dir() / "config" / "license.json"
    machine_hash: str | None = None
    try:
        machine_hash = get_machine_hash()
        return load_and_validate_license(license_path, machine_hash)
    except LicenseValidationError as error:
        show_license_error(error, machine_hash, license_path)
        return None

__all__ = [
    "LicenseErrorCode",
    "LicensePayload",
    "LicenseValidationError",
    "ensure_license_or_exit",
    "get_machine_hash",
    "load_and_validate_license",
]
