from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


# Production public keys are inserted here after generation in the private
# AutoPrint-License-Tools repository, for example:
# "autoprint-license-ed25519-2026-01": base64.b64decode("...")
# Keeping only issuer public keys here avoids creating a development backdoor.
PRODUCTION_PUBLIC_KEYS: dict[str, bytes] = {
    "autoprint-license-ed25519-2026-09": base64.b64decode(
        "lo94pqcubiDdSVhFrc+RUCR9TgKQnqrpk8biOmzUFHw="
    ),
}


def canonicalize_payload(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def verify_payload_signature(
    payload: object,
    signature_b64: str,
    key_id: str,
    keyring: Mapping[str, bytes],
) -> None:
    public_key_bytes = keyring[key_id]
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidSignature from exc
    Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
        signature,
        canonicalize_payload(payload),
    )
