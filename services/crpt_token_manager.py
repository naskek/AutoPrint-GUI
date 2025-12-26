# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import json
import threading
import urllib.error
import urllib.request
import re
import zlib

from services.crpt_settings import load_crpt_settings
from services import crpt_auth

FALLBACK_EXP_SECONDS = 21600


class CrptTokenError(Exception):
    def __init__(self, message: str, user_message: str | None = None, details: str | None = None):
        super().__init__(message)
        self.user_message = user_message or message
        self.details = details


def _now_utc():
    return dt.datetime.now(dt.timezone.utc)


def _request_json(
    method: str,
    url: str,
    headers: dict | None,
    body: dict | list | None,
    timeout: float,
    retries: int,
    allow_auth_header: bool = True,
):
    if headers is None:
        headers = {}
    if not allow_auth_header:
        for k in headers.keys():
            if str(k).lower() == "authorization":
                raise CrptTokenError(
                    "auth header not allowed",
                    "Authorization header \u043d\u0435 \u0434\u043e\u043b\u0436\u0435\u043d \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c\u0441\u044f \u0434\u043b\u044f /auth/*",
                )
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    headers.setdefault("User-Agent", "AutoPrint/CRPT")
    last_exc = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                raw = resp.read()
                text, meta = _decode_response_body(raw, resp.headers)
                return status, text, meta
        except urllib.error.HTTPError as e:
            raw = e.read()
            text, meta = _decode_response_body(raw, getattr(e, "headers", None))
            return e.code, text, meta
        except Exception as e:
            last_exc = e
            if attempt < retries:
                continue
            raise CrptTokenError(str(e), "\u0421\u0435\u0442\u0435\u0432\u0430\u044f \u043e\u0448\u0438\u0431\u043a\u0430", str(e))
    raise CrptTokenError(str(last_exc), "\u0421\u0435\u0442\u0435\u0432\u0430\u044f \u043e\u0448\u0438\u0431\u043a\u0430", str(last_exc))


def _decode_response_body(raw: bytes, headers) -> tuple[str, dict]:
    if raw is None:
        return "", {"content_type": "", "content_encoding": "", "charset": "", "body_len": 0}
    data = raw
    content_type = ""
    content_encoding = ""
    try:
        if headers:
            content_type = str(headers.get("Content-Type", "") or "")
            content_encoding = str(headers.get("Content-Encoding", "") or "").lower()
    except Exception:
        content_type = ""
        content_encoding = ""
    try:
        if "gzip" in content_encoding:
            data = gzip.decompress(raw)
        elif "deflate" in content_encoding:
            try:
                data = zlib.decompress(raw)
            except Exception:
                data = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        data = raw
    if not content_encoding and raw[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(raw)
        except Exception:
            data = raw
    charset = ""
    try:
        lower = content_type.lower()
        if "charset=" in lower:
            charset = lower.split("charset=", 1)[1].split(";")[0].strip().strip('"')
    except Exception:
        charset = ""
    if not charset:
        charset = "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except Exception:
        text = data.decode("utf-8", errors="replace")
    meta = {
        "content_type": content_type,
        "content_encoding": content_encoding,
        "charset": charset,
        "body_len": len(text),
        "body_prefix": text[:200],
    }
    return text, meta


def _trim_body(body: str | None, limit: int = 1500) -> str:
    text = str(body or "")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _redact_auth_key_body(body: str | None) -> str:
    text = str(body or "")
    if not text:
        return ""
    redacted = text.lstrip("\ufeff")
    redacted = re.sub(r'("data"\s*:\s*")[^"]*(")', r'\1<redacted>\2', redacted)
    redacted = re.sub(r"('data'\s*:\s*')[^']*(')", r"\1<redacted>\2", redacted)
    redacted = re.sub(r'("uuid"\s*:\s*")[^"]*(")', r'\1<redacted>\2', redacted)
    redacted = re.sub(r"('uuid'\s*:\s*')[^']*(')", r"\1<redacted>\2", redacted)
    return _trim_body(redacted, limit=500)


def _auth_key_details(url: str, status: int, body: str | None, meta: dict | None = None, error: Exception | None = None) -> str:
    text = str(body or "")
    details = {
        "url": url,
        "status": status,
        "body_len": len(text),
    }
    if meta:
        details["content_type"] = meta.get("content_type", "")
        details["content_encoding"] = meta.get("content_encoding", "")
        details["body_len"] = meta.get("body_len", len(text))
        prefix = str(meta.get("body_prefix", "") or "")
        safe_prefix = _redact_auth_key_body(prefix) if prefix else ""
        if safe_prefix:
            details["body_prefix"] = safe_prefix
    if error:
        details["error"] = str(error)
    return json.dumps(details, ensure_ascii=False)


def _load_json_payload(text: str | None) -> dict | None:
    if not text:
        return None
    raw = str(text).lstrip("\ufeff")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


def _join_url(base: str, path: str) -> str:
    base = (base or "").rstrip("/")
    if not base:
        raise CrptTokenError("missing base url", "\u041d\u0435 \u0437\u0430\u0434\u0430\u043d CRPT_TRUE_API_BASE_URL")
    p = str(path or "")
    if p.startswith("http://") or p.startswith("https://"):
        raise CrptTokenError("absolute url not allowed", "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 path \u043e\u0442\u043d\u043e\u0441\u0438\u0442\u0435\u043b\u044c\u043d\u043e CRPT_TRUE_API_BASE_URL")
    if not p.startswith("/"):
        p = "/" + p
    return f"{base}{p}"


def _normalize_token(token: str | None) -> str:
    s = str(token or "").strip().strip('"')
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    if any(ch.isspace() for ch in s):
        s = "".join(ch for ch in s if not ch.isspace())
    return s


def _token_is_jwt(token: str) -> bool:
    if not token:
        return False
    if token.count(".") != 2:
        return False
    if any(ch.isspace() for ch in token):
        return False
    return True


def _token_is_uuid_like(token: str) -> bool:
    if not token:
        return False
    s = token.strip()
    if len(s) == 36 and s[8] == "-" and s[13] == "-" and s[18] == "-" and s[23] == "-":
        parts = s.split("-")
        if len(parts) == 5 and all(re.fullmatch(r"[0-9a-fA-F]+", p or "") for p in parts):
            return True
    if len(s) == 32 and re.fullmatch(r"[0-9a-fA-F]{32}", s):
        return True
    return False


def _token_is_valid(token: str) -> bool:
    return _token_is_jwt(token) or _token_is_uuid_like(token)


def _token_diag(raw_token: str | None, normalized: str, token_source: str | None = None) -> str:
    raw = str(raw_token or "")
    return json.dumps({
        "token_len": len(normalized),
        "jwt_dots": normalized.count("."),
        "is_uuid_like": _token_is_uuid_like(normalized),
        "has_space": any(ch.isspace() for ch in raw),
        "starts_with_bearer": raw.strip().lower().startswith("bearer "),
        "token_prefix_after_bearer": normalized[:8],
        "token_source": token_source or "",
    }, ensure_ascii=False)


def _ensure_valid_token(tm: "CrptTokenManager") -> str:
    raw = tm.get_token_sync()
    token = _normalize_token(raw)
    if not _token_is_valid(token):
        tm.force_refresh_sync()
        raw = tm.get_token_sync()
        token = _normalize_token(raw)
    if not _token_is_valid(token):
        raise CrptTokenError(
            "invalid token",
            "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442 \u0442\u043e\u043a\u0435\u043d\u0430",
            _token_diag(raw, token, getattr(tm, "token_source", None)),
        )
    return token


def _extract_token_from_response(resp: dict) -> str | None:
    if not isinstance(resp, dict):
        return None
    token = resp.get("token")
    if isinstance(token, str) and token.strip():
        return token
    return None


def _collect_token_candidates(resp: dict) -> list[tuple[str, str]]:
    if not isinstance(resp, dict):
        return []
    candidates: list[tuple[str, str]] = []
    keys = ("token", "jwt", "access_token", "accessToken", "authToken", "auth_token", "bearerToken")
    for key in keys:
        value = resp.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append((key, value))
    for parent in ("data", "result"):
        sub = resp.get(parent)
        if isinstance(sub, dict):
            for key in keys:
                value = sub.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append((f"{parent}.{key}", value))
    return candidates


def _summarize_token_candidates(candidates: list[tuple[str, str]]) -> dict:
    summary = {}
    for key, value in candidates:
        norm = _normalize_token(value)
        summary[key] = {
            "token_len": len(norm),
            "jwt_dots": norm.count("."),
            "is_uuid_like": _token_is_uuid_like(norm),
        }
    return summary


def _select_token_from_response(resp: dict) -> tuple[str | None, str | None, dict]:
    candidates = _collect_token_candidates(resp)
    summary = _summarize_token_candidates(candidates)
    for key, value in candidates:
        norm = _normalize_token(value)
        if _token_is_jwt(norm):
            return norm, key, summary
    if candidates:
        key, value = candidates[0]
        return _normalize_token(value), key, summary
    return None, None, summary


def _request_json_with_settings(
    tm: "CrptTokenManager",
    settings,
    method: str,
    path: str,
    json_body: dict | list | None,
    auth: bool,
    return_meta: bool = False,
):
    normalized_path = str(path or "")
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if normalized_path.startswith("/auth/"):
        if auth:
            raise CrptTokenError(
                "auth header not allowed",
                "Authorization header \u043d\u0435 \u0434\u043e\u043b\u0436\u0435\u043d \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c\u0441\u044f \u0434\u043b\u044f /auth/*",
            )
        auth = False
    url = _join_url(settings.true_api_base_url, normalized_path)
    headers = {"Accept": "application/json", "User-Agent": "AutoPrint/CRPT"}
    if auth:
        token = _ensure_valid_token(tm)
        headers["Authorization"] = f"Bearer {token}"
    status, text, meta = _request_json(
        method,
        url,
        headers,
        json_body,
        settings.timeout,
        settings.retries,
        allow_auth_header=auth,
    )
    if auth and status in (401, 403):
        tm.force_refresh_sync()
        token = _ensure_valid_token(tm)
        headers["Authorization"] = f"Bearer {token}"
        status, text, meta = _request_json(
            method,
            url,
            headers,
            json_body,
            settings.timeout,
            settings.retries,
            allow_auth_header=True,
        )
    if return_meta:
        return status, text, meta
    return status, text


def build_url(path: str, settings=None) -> str:
    if settings is None:
        settings = load_crpt_settings()
    return _join_url(settings.true_api_base_url, path)


class CrptTokenManager:
    def __init__(
        self,
        true_api_base_url: str | None = None,
        cert_thumbprint: str | None = None,
        safety_seconds: int | None = None,
        timeout_seconds: float | None = None,
    ):
        self._lock = threading.Lock()
        self.token = None
        self.token_exp = None
        self.token_received_at = None
        self.token_source = ""
        self.token_type = ""
        self.last_error = None
        self._base_url_override = true_api_base_url
        self._cert_thumbprint_override = cert_thumbprint
        self._safety_override = safety_seconds
        self._timeout_override = timeout_seconds

    def _set_error(self, message: str | None, details: str | None = None):
        self.last_error = message
        if details:
            self.last_error = f"{message}: {details}"

    def _load_settings(self):
        settings = load_crpt_settings()
        if self._base_url_override:
            settings.true_api_base_url = self._base_url_override
        if self._cert_thumbprint_override:
            settings.cert_thumbprint = self._cert_thumbprint_override
        if self._safety_override is not None:
            settings.token_refresh_safety_seconds = self._safety_override
        if self._timeout_override is not None:
            settings.timeout = float(self._timeout_override)
        return settings

    def _ensure_config(self, settings):
        if not (settings.oms_connection or settings.token_provider_inn):
            raise CrptTokenError(
                "missing config",
                "\u041d\u0435 \u0437\u0430\u0434\u0430\u043d CRPT_OMS_CONNECTION \u0438\u043b\u0438 CRPT_TOKEN_PROVIDER_INN",
            )
        if not settings.cert_thumbprint:
            raise CrptTokenError(
                "missing config",
                "\u041d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d \u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442",
            )

    def _refresh_sync(self):
        settings = self._load_settings()
        self._ensure_config(settings)

        key_path = "/auth/key"
        status, body, meta = _request_json_with_settings(self, settings, "GET", key_path, None, auth=False, return_meta=True)
        key_url = _join_url(settings.true_api_base_url, key_path)
        key_details = json.dumps({"url": key_url, "status": status}, ensure_ascii=False)
        if status < 200 or status >= 300:
            raise CrptTokenError("auth/key failed", "\u041e\u0448\u0438\u0431\u043a\u0430 /auth/key", key_details)
        payload = _load_json_payload(body)
        if payload is None:
            raise CrptTokenError(
                "invalid auth/key",
                "\u041e\u0448\u0438\u0431\u043a\u0430 \u0440\u0430\u0437\u0431\u043e\u0440\u0430 /auth/key",
                _auth_key_details(key_url, status, body, meta),
            )
        uuid = payload.get("uuid") or payload.get("keyId") or payload.get("id")
        data = payload.get("data")
        if not uuid or not data:
            raise CrptTokenError(
                "auth/key missing fields",
                "\u041d\u0435\u043f\u043e\u043b\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 /auth/key",
                _auth_key_details(key_url, status, body, meta),
            )

        try:
            signature = crpt_auth.sign_data_base64(str(data), settings.cert_thumbprint)
        except crpt_auth.CrptAuthError as e:
            raise CrptTokenError("sign failed", e.user_message, str(e))

        if settings.token_provider_inn:
            signin_path = "/auth/simpleSignIn"
            signin_body = {"uuid": uuid, "data": signature, "tokenProviderInn": settings.token_provider_inn}
        else:
            signin_path = f"/auth/simpleSignIn/{settings.oms_connection}"
            signin_body = {"uuid": uuid, "data": signature}
        status, body = _request_json_with_settings(
            self,
            settings,
            "POST",
            signin_path,
            signin_body,
            auth=False,
        )
        if status < 200 or status >= 300:
            details = json.dumps({
                "url": _join_url(settings.true_api_base_url, signin_path),
                "status": status,
                "body": _trim_body(body),
            }, ensure_ascii=False)
            raise CrptTokenError("signIn failed", "\u041e\u0448\u0438\u0431\u043a\u0430 /auth/simpleSignIn", details)
        try:
            resp = json.loads(body) if body else {}
        except Exception as e:
            details = json.dumps({
                "url": _join_url(settings.true_api_base_url, signin_path),
                "status": status,
                "body": _trim_body(body),
                "error": str(e),
            }, ensure_ascii=False)
            raise CrptTokenError(
                "invalid signIn response",
                "\u041e\u0448\u0438\u0431\u043a\u0430 \u0440\u0430\u0437\u0431\u043e\u0440\u0430 /auth/simpleSignIn",
                details,
            )
        token, token_source, token_summary = _select_token_from_response(resp)
        if not token:
            details = json.dumps({
                "response_keys": list(resp.keys()) if isinstance(resp, dict) else [],
                "candidates": token_summary,
            }, ensure_ascii=False)
            raise CrptTokenError("token missing", "\u041e\u0442\u0432\u0435\u0442 \u0431\u0435\u0437 \u0442\u043e\u043a\u0435\u043d\u0430", details)
        normalized = _normalize_token(token)
        if not _token_is_valid(normalized):
            raise CrptTokenError(
                "invalid token",
                "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442 \u0442\u043e\u043a\u0435\u043d\u0430",
                _token_diag(token, normalized, token_source or "token"),
            )
        self.token = normalized
        self.token_source = token_source or "token"
        if _token_is_jwt(self.token):
            self.token_type = "jwt"
            exp = crpt_auth.parse_jwt_exp(self.token)
        else:
            self.token_type = "uuid" if _token_is_uuid_like(self.token) else "opaque"
            exp = None
        now = _now_utc()
        self.token_exp = exp if exp else (now + dt.timedelta(seconds=FALLBACK_EXP_SECONDS))
        self.token_received_at = now
        self._set_error(None)

    def force_refresh_sync(self):
        with self._lock:
            try:
                self._refresh_sync()
            except CrptTokenError as e:
                self._set_error(e.user_message, e.details)
                raise
        return self.token

    def get_token_sync(self):
        settings = self._load_settings()
        self._ensure_config(settings)
        if not self.token:
            return self.force_refresh_sync()
        now = _now_utc()
        if self.token_exp:
            seconds_left = (self.token_exp - now).total_seconds()
            if seconds_left <= settings.token_refresh_safety_seconds:
                return self.force_refresh_sync()
        return self.token

    def force_refresh(self) -> str:
        return self.force_refresh_sync()

    async def get_token(self) -> str:
        return await asyncio.to_thread(self.get_token_sync)

    async def force_refresh_async(self):
        return await asyncio.to_thread(self.force_refresh_sync)

    def request_json(self, method: str, path: str, json_body: dict | list | None = None, auth: bool = False):
        settings = self._load_settings()
        return _request_json_with_settings(self, settings, method, path, json_body, auth)

    def request_sync(self, method: str, path: str, body: dict | list | None = None, auth: bool = True):
        return self.request_json(method, path, body, auth)

    async def request(self, method: str, path: str, body: dict | list | None = None, auth: bool = True):
        return await asyncio.to_thread(self.request_sync, method, path, body, auth)

    def health_sync(self, sign_test: bool = False):
        settings = self._load_settings()
        crypto_ok, crypto_err = crpt_auth.crypto_pro_available()
        certs = []
        selected = None
        if crypto_ok:
            try:
                certs = crpt_auth.list_certificates()
            except Exception as e:
                crypto_ok = False
                self._set_error(getattr(e, "user_message", None) or str(e))
        else:
            if crypto_err:
                self._set_error(crypto_err)
        if settings.cert_thumbprint:
            for c in certs:
                if c.thumbprint == settings.cert_thumbprint:
                    selected = c
                    break
        cert_ok = bool(selected and selected.has_private_key)
        sign_ok = False
        if sign_test and selected:
            try:
                _ = crpt_auth.sign_data_base64("test", settings.cert_thumbprint)
                sign_ok = True
            except Exception as e:
                self._set_error(getattr(e, "user_message", None) or str(e))
                sign_ok = False
        token_present = bool(self.token)
        seconds_left = None
        if self.token_exp:
            seconds_left = int((self.token_exp - _now_utc()).total_seconds())
        token_norm = _normalize_token(self.token)
        token_len = len(token_norm) if token_present else 0
        token_jwt_dots = token_norm.count(".") if token_present else 0
        received_at = None
        if self.token_received_at:
            try:
                received_at = self.token_received_at.isoformat(timespec="seconds")
            except Exception:
                received_at = str(self.token_received_at)
        auth_mode = "inn" if settings.token_provider_inn else "oms"
        out = {
            "baseUrl": settings.true_api_base_url,
            "pg": settings.pg,
            "authMode": auth_mode,
            "cryptoProAvailable": crypto_ok,
            "selectedCert": None if not selected else {
                "thumbprint": selected.thumbprint,
                "subject": selected.subject,
                "validTo": selected.valid_to,
            },
            "certOk": cert_ok,
            "signTestOk": sign_ok,
            "tokenPresent": token_present,
            "tokenLen": token_len,
            "tokenJwtDots": token_jwt_dots,
            "tokenType": self.token_type,
            "tokenReceivedAt": received_at,
            "tokenExpiresAt": self.token_exp.isoformat() if self.token_exp else None,
            "secondsLeft": seconds_left,
            "lastError": self.last_error,
        }
        return out, certs

    async def health(self, sign_test: bool = False):
        return await asyncio.to_thread(self.health_sync, sign_test)


_TOKEN_MANAGER = CrptTokenManager()


def get_token_manager() -> CrptTokenManager:
    return _TOKEN_MANAGER


def request_json(method: str, path: str, json_body: dict | list | None = None, auth: bool = False):
    return _TOKEN_MANAGER.request_json(method, path, json_body, auth)
