# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import datetime as dt
import json
from dataclasses import dataclass

try:
    import pythoncom
    import win32com.client
except Exception:  # pragma: no cover - optional dependency
    pythoncom = None
    win32com = None

CADESCOM_STORE_LOCATION_CURRENT_USER = 2
CADESCOM_STORE_NAME_MY = "My"
CADESCOM_STORE_OPEN_READ_ONLY = 0
CADESCOM_CERTIFICATE_FIND_SHA1_HASH = 0
CADESCOM_CADES_BES = 1


@dataclass
class CertInfo:
    thumbprint: str
    subject: str
    issuer: str
    valid_from: str
    valid_to: str
    has_private_key: bool


class CrptAuthError(Exception):
    def __init__(self, message: str, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or message


def _com_init():
    if pythoncom:
        pythoncom.CoInitialize()


def _com_uninit():
    if pythoncom:
        pythoncom.CoUninitialize()


def _ensure_com():
    if not win32com:
        raise CrptAuthError("win32com not available", "\u041a\u0440\u0438\u043f\u0442\u043e\u041f\u0440\u043e \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d (\u043d\u0435\u0442 win32com).")


def _humanize_error(err: Exception) -> str:
    msg = str(err)
    low = msg.lower()
    mapping = [
        ("0x80090016", "\u0417\u0430\u043a\u0440\u044b\u0442\u044b\u0439 \u043a\u043b\u044e\u0447 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d"),
        ("0x80090030", "\u041d\u043e\u0441\u0438\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"),
        ("0x8010006b", "\u041d\u043e\u0441\u0438\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"),
        ("keyset does not exist", "\u0417\u0430\u043a\u0440\u044b\u0442\u044b\u0439 \u043a\u043b\u044e\u0447 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d"),
        ("private key", "\u0417\u0430\u043a\u0440\u044b\u0442\u044b\u0439 \u043a\u043b\u044e\u0447 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d"),
        ("not found", "\u0421\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"),
        ("certificate", "\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442\u0430"),
        ("pin", "\u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0432\u0432\u043e\u0434 PIN"),
    ]
    for needle, human in mapping:
        if needle in low:
            return human
    return "\u041e\u0448\u0438\u0431\u043a\u0430 CryptoPro"


def _open_store():
    store = win32com.client.Dispatch("CAdESCOM.Store")
    store.Open(CADESCOM_STORE_LOCATION_CURRENT_USER, CADESCOM_STORE_NAME_MY, CADESCOM_STORE_OPEN_READ_ONLY)
    return store


def crypto_pro_available():
    try:
        _ensure_com()
        _com_init()
        store = _open_store()
        try:
            _ = store.Certificates.Count
        finally:
            try:
                store.Close()
            except Exception:
                pass
        return True, None
    except CrptAuthError as e:
        return False, e.user_message
    except Exception as e:
        return False, _humanize_error(e)
    finally:
        _com_uninit()


def list_certificates() -> list[CertInfo]:
    _ensure_com()
    _com_init()
    try:
        store = _open_store()
        certs = store.Certificates
        out: list[CertInfo] = []
        for i in range(1, certs.Count + 1):
            cert = certs.Item(i)
            thumb = "".join((cert.Thumbprint or "").split()).upper()
            subject = str(cert.SubjectName or "")
            issuer = str(cert.IssuerName or "")
            valid_from = str(cert.ValidFromDate or "")
            valid_to = str(cert.ValidToDate or "")
            has_private = False
            try:
                has_private = bool(getattr(cert, "HasPrivateKey", False))
                if has_private:
                    _ = cert.PrivateKey
            except Exception:
                has_private = False
            out.append(CertInfo(
                thumbprint=thumb,
                subject=subject,
                issuer=issuer,
                valid_from=valid_from,
                valid_to=valid_to,
                has_private_key=has_private,
            ))
        return out
    except Exception as e:
        raise CrptAuthError(str(e), _humanize_error(e))
    finally:
        _com_uninit()


def _find_cert_by_thumbprint(store, thumbprint: str):
    tp = "".join((thumbprint or "").split()).upper()
    if not tp:
        return None
    try:
        certs = store.Certificates.Find(CADESCOM_CERTIFICATE_FIND_SHA1_HASH, tp, False)
        if certs.Count >= 1:
            return certs.Item(1)
    except Exception:
        return None
    return None


def sign_data_base64(data: str, thumbprint: str) -> str:
    _ensure_com()
    _com_init()
    try:
        store = _open_store()
        cert = _find_cert_by_thumbprint(store, thumbprint)
        if not cert:
            raise CrptAuthError("cert not found", "\u0421\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        signer = win32com.client.Dispatch("CAdESCOM.CPSigner")
        signer.Certificate = cert
        signer.CheckCertificate = True
        signed_data = win32com.client.Dispatch("CAdESCOM.CadesSignedData")
        signed_data.Content = data
        signature = signed_data.SignCades(signer, CADESCOM_CADES_BES, False)
        return str(signature or "").replace("\r", "").replace("\n", "")
    except CrptAuthError:
        raise
    except Exception as e:
        raise CrptAuthError(str(e), _humanize_error(e))
    finally:
        _com_uninit()


def parse_jwt_exp(token: str):
    if not token:
        return None
    parts = str(token).split(".")
    if len(parts) < 2:
        return None
    try:
        payload = parts[1]
        pad = "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode(payload + pad)
        obj = json.loads(data.decode("utf-8", errors="replace"))
        exp = obj.get("exp")
        if exp is None:
            return None
        return dt.datetime.fromtimestamp(int(exp), tz=dt.timezone.utc)
    except Exception:
        return None
