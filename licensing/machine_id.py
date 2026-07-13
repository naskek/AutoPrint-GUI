from __future__ import annotations

import hashlib
import re
import winreg

from .license_manager import LicenseErrorCode, LicenseValidationError


_MACHINE_GUID_KEY = r"SOFTWARE\Microsoft\Cryptography"
_MACHINE_GUID_VALUE = "MachineGuid"
_NORMALIZED_GUID_RE = re.compile(r"[0-9a-f]{32}\Z")
_HASH_PREFIX = b"autoprint-gui\0machineguid\0"


def get_machine_hash() -> str:
    try:
        access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            _MACHINE_GUID_KEY,
            0,
            access,
        ) as key:
            raw_value, _value_type = winreg.QueryValueEx(key, _MACHINE_GUID_VALUE)
        if not isinstance(raw_value, str):
            raise TypeError("MachineGuid must be a string")
        normalized = re.sub(r"\s+", "", raw_value)
        normalized = normalized.replace("{", "").replace("}", "").replace("-", "").lower()
        if not _NORMALIZED_GUID_RE.fullmatch(normalized):
            raise ValueError("MachineGuid has an unexpected format")
    except (OSError, TypeError, ValueError) as exc:
        raise LicenseValidationError(
            LicenseErrorCode.MACHINE_ID_UNAVAILABLE,
            "Windows MachineGuid is unavailable or invalid.",
        ) from exc

    digest = hashlib.sha256(_HASH_PREFIX + normalized.encode("ascii")).hexdigest()
    return f"sha256:{digest}"
