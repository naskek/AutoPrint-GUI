# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from urllib.parse import quote
from typing import Any

from services.crpt_settings import load_crpt_settings
from services.crpt_token_manager import (
    get_token_manager,
    CrptTokenError,
    request_json,
    build_url,
)

GS = chr(29)


@dataclass
class CrptConfig:
    timeout: float
    max_codes_per_request: int
    concurrency: int
    retries: int
    pg: str


@dataclass
class CrptCodeResult:
    index: int
    code: str
    normalized: str
    status: str
    status_ex: str
    explain: str
    introduced: bool | None
    severity: str
    raw: Any = None
    error: str | None = None
    error_details: str | None = None


class CrptClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CrptAuthError(CrptClientError):
    pass


class CrptServiceError(CrptClientError):
    pass


class CrptRequestError(CrptClientError):
    pass


class CrptTransportError(CrptClientError):
    pass


def crpt_check_connection_sync(use_info: bool = False):
    cfg = get_crpt_config()
    if not cfg.pg:
        return False, "\u041d\u0435 \u0437\u0430\u0434\u0430\u043d CRPT_PG (product group). \u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0432 .env, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: CRPT_PG=grocery", "error", ""
    path = _path_with_pg("/cises/info" if use_info else "/cises/short/list", cfg.pg)
    try:
        status_code, body = request_json("POST", path, [], auth=True)
    except CrptTokenError as e:
        return False, e.user_message, "error", e.details
    if status_code in (200, 204, 400, 422):
        return True, "\u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u043e", "ok", body
    if status_code in (401, 403):
        details = _auth_error_details(path, status_code, auth_present=True, body=body)
        return False, "\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430/\u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u043e\u0434\u043f\u0438\u0441\u0438/\u043d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 omsConnection/\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442", "error", details
    if status_code == 404:
        return False, "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 CRPT_TRUE_API_BASE_URL", "error", body
    if status_code >= 500:
        return False, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u0435\u0440\u0432\u0438\u0441\u0430 \u0413\u0418\u0421 \u041c\u0422", "error", body
    return False, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0440\u043e\u0441\u0430", "error", body


def get_crpt_config() -> CrptConfig:
    settings = load_crpt_settings()
    timeout = settings.timeout
    max_codes = settings.max_codes_per_request
    concurrency = settings.concurrency
    retries = settings.retries
    pg = (settings.pg or "").strip()
    return CrptConfig(
        timeout=timeout,
        max_codes_per_request=max_codes,
        concurrency=concurrency,
        retries=retries,
        pg=pg,
    )


def _normalize_token_for_diag(token: str | None) -> str:
    return str(token or "").strip().strip('"')


def _auth_error_details(path: str, status_code: int, auth_present: bool = True, body: str | None = None) -> str:
    tm = get_token_manager()
    token = _normalize_token_for_diag(getattr(tm, "token", None))
    settings = load_crpt_settings()
    text = str(body or "")
    if len(text) > 500:
        text = text[:497] + "..."
    return json.dumps({
        "url": build_url(path),
        "pg": settings.pg,
        "status": status_code,
        "body": text,
        "auth_present": bool(auth_present),
        "token_len": len(token),
        "jwt_dots": token.count("."),
        "token_type": getattr(tm, "token_type", ""),
        "token_source": getattr(tm, "token_source", ""),
        "token_prefix_after_bearer": token[:8],
    }, ensure_ascii=False)


def _payload_diagnostics(payload: Any) -> dict:
    info: dict[str, Any] = {
        "json_body_type": type(payload).__name__,
    }
    if isinstance(payload, list):
        info["payload_count"] = len(payload)
        if payload:
            sample = str(payload[0])
            lower = sample.lower()
            info["sample_len"] = len(sample)
            info["sample_has_gs"] = GS in sample
            info["sample_gs_count"] = sample.count(GS)
            info["sample_has_literal_u001d"] = "\\u001d" in lower
            info["sample_has_literal_x1d"] = "\\x1d" in lower
    elif isinstance(payload, dict):
        try:
            info["payload_keys"] = list(payload.keys())
        except Exception:
            pass
    return info


def _request_error_details(path: str, status_code: int, body: str | None, payload: Any | None = None) -> str:
    settings = load_crpt_settings()
    text = str(body or "")
    if len(text) > 500:
        text = text[:497] + "..."
    details: dict[str, Any] = {
        "url": build_url(path),
        "pg": settings.pg,
        "status": status_code,
        "body": text,
    }
    if payload is not None:
        details.update(_payload_diagnostics(payload))
    return json.dumps(details, ensure_ascii=False)


def _path_with_pg(path: str, pg: str | None) -> str:
    if not pg:
        return path
    p = str(path or "")
    delim = "&" if "?" in p else "?"
    return f"{p}{delim}pg={quote(str(pg))}"


def _log_diag(logger, msg: str):
    if not logger:
        return
    try:
        if callable(logger):
            logger(msg)
            return
        log_fn = getattr(logger, "log", None)
        if callable(log_fn):
            log_fn(msg)
    except Exception:
        pass


def normalize_code(code: str) -> str:
    s = "" if code is None else str(code)
    # Convert escaped GS sequences (including double-escaped) to ASCII GS.
    s = re.sub(r"\\+u001d", GS, s, flags=re.IGNORECASE)
    s = re.sub(r"\\+x1d", GS, s, flags=re.IGNORECASE)
    s = s.replace("\u001d", GS)
    return s.strip()


def _normalize_to_ki_with_diag(code: str) -> tuple[str, dict]:
    s = normalize_code(code)
    has_gs = GS in s
    if has_gs:
        s = s.split(GS, 1)[0]
    s = s.strip()
    suspicious = (not s.startswith("01")) or ("21" not in s)
    return s, {
        "sent_ki_len": len(s),
        "has_gs_before_trim": has_gs,
        "suspicious_format": suspicious,
    }


def normalize_to_ki(code: str) -> str:
    ki, _diag = _normalize_to_ki_with_diag(code)
    return ki


def _format_ki_sample(ki: str) -> str:
    s = str(ki or "")
    return f"{s[:8]}(len={len(s)})"


def parse_codes(text: str, dedupe: bool = False) -> list[str]:
    if text is None:
        return []
    out: list[str] = []
    seen: set[str] = set()
    # Split only on CR/LF to keep ASCII GS (0x1D) inside codes.
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    for raw in s.split("\n"):
        if raw is None:
            continue
        code = normalize_code(raw)
        if not code:
            continue
        if dedupe:
            key = code
            if key in seen:
                continue
            seen.add(key)
        out.append(code)
    return out


def interpret_status(status: str, status_ex: str | None) -> tuple[str, bool | None, str]:
    s = (status or "").strip().upper()
    if not s:
        return ("\u041d\u0435\u0442 \u0441\u0442\u0430\u0442\u0443\u0441\u0430", None, "warn")
    if s == "INTRODUCED":
        return ("\u0412 \u043e\u0431\u043e\u0440\u043e\u0442\u0435 (\u0432\u0432\u0435\u0434\u0451\u043d)", True, "ok")
    if s == "APPLIED":
        return ("\u041d\u0430\u043d\u0435\u0441\u0451\u043d/\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043b\u0435\u043d, \u043d\u0435 \u0432\u0432\u0435\u0434\u0451\u043d", False, "warn")
    if s in ("NOT_FOUND", "NOTFOUND", "UNKNOWN"):
        return ("\u041a\u043e\u0434 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", None, "error")
    withdrawn_marks = ("WITHDRAW", "CANCEL", "ANNUL", "RETIRED", "OUT", "WRITE", "BLOCK")
    if any(m in s for m in withdrawn_marks):
        return ("\u0412\u044b\u0432\u0435\u0434\u0435\u043d/\u0430\u043d\u043d\u0443\u043b\u0438\u0440\u043e\u0432\u0430\u043d", False, "error")
    return (f"\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439 \u0441\u0442\u0430\u0442\u0443\u0441: {status}", None, "info")


def _extract_items(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("codes", "cises", "items", "list", "data", "result", "results", "cisInfo", "cisInfos", "cisInfoList"):
        if isinstance(data.get(key), list):
            return [i for i in data.get(key) if isinstance(i, dict)]
    return []


def _get_item_field(item: dict, keys: list[str]) -> str:
    for k in keys:
        v = item.get(k)
        if v is None:
            continue
        return str(v)
    return ""


def _looks_like_codes_response(data: Any) -> bool:
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                if any(k in item for k in (
                    "cis", "code", "sgtin", "cisFull", "cis_full", "requestedCis",
                    "status", "state", "statusCode", "errorMessage", "errorCode", "result"
                )):
                    return True
        return False
    if isinstance(data, dict):
        for key in ("codes", "cises", "items", "list", "data", "result", "results", "cisInfo", "cisInfos", "cisInfoList"):
            if isinstance(data.get(key), list):
                return True
        if any(k in data for k in ("cis", "code", "sgtin", "status", "state", "statusCode", "errorMessage", "errorCode", "requestedCis")):
            return True
        sub = data.get("result")
        if isinstance(sub, dict):
            if any(k in sub for k in ("cis", "code", "sgtin", "status", "state", "statusCode", "errorMessage", "errorCode", "requestedCis")):
                return True
    return False


def _parse_short_list_response(data: Any) -> list[dict]:
    items = _extract_items(data)
    out = []
    for item in items:
        result = item.get("result") if isinstance(item.get("result"), dict) else None
        code = _get_item_field(item, ["cis", "code", "sgtin", "cisFull", "cis_full", "requestedCis", "cisRequested"])
        if not code and result:
            code = _get_item_field(result, ["cis", "code", "sgtin", "cisFull", "cis_full", "requestedCis", "cisRequested"])
        status = _get_item_field(item, ["status", "state", "statusCode"])
        if not status and result:
            status = _get_item_field(result, ["status", "state", "statusCode"])
        status_ex = _get_item_field(item, ["statusEx", "status_ex", "statusDescription", "error", "errorMessage", "message"])
        if not status_ex and result:
            status_ex = _get_item_field(result, ["statusEx", "status_ex", "statusDescription", "error", "errorMessage", "message"])
        error_code = _get_item_field(item, ["errorCode", "error_code"])
        if not error_code and result:
            error_code = _get_item_field(result, ["errorCode", "error_code"])
        if not status and error_code:
            status = error_code
        if status == "404":
            status = "NOT_FOUND"
        if not status and status_ex:
            low = status_ex.lower()
            if "не найден" in low or "not found" in low:
                status = "NOT_FOUND"
        out.append({
            "code": code,
            "status": status,
            "statusEx": status_ex,
            "raw": item,
        })
    return out


def _make_error_result(item: dict, status: str, explain: str, severity: str, error: str, details: str | None):
    return CrptCodeResult(
        index=item["index"],
        code=item["code"],
        normalized=item["normalized"],
        status=status,
        status_ex="",
        explain=explain,
        introduced=None,
        severity=severity,
        raw={"error": error, "details": details},
        error=error,
        error_details=details,
    )


def _check_chunk_sync(chunk: list[dict], use_info: bool, logger=None) -> list[CrptCodeResult]:
    cfg = get_crpt_config()
    if not cfg.pg:
        raise CrptClientError("\u041d\u0435 \u0437\u0430\u0434\u0430\u043d CRPT_PG (product group). \u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0432 .env, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: CRPT_PG=grocery")
    path = _path_with_pg("/cises/info" if use_info else "/cises/short/list", cfg.pg)
    payload = [item["normalized"] for item in chunk]
    if logger:
        samples = [_format_ki_sample(item.get("normalized", "")) for item in chunk[:2]]
        sample_text = ", ".join(samples) if samples else "-"
        suspicious_count = sum(1 for item in chunk if (item.get("diag") or {}).get("suspicious_format"))
        msg = f"[CRPT] \u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043a\u043e\u0434\u043e\u0432: pg={cfg.pg}, sample_ki=[{sample_text}]"
        if suspicious_count:
            msg += f", \u0444\u043e\u0440\u043c\u0430\u0442 \u0441\u043e\u043c\u043d\u0438\u0442\u0435\u043b\u0435\u043d={suspicious_count}"
        _log_diag(logger, msg)
    try:
        status_code, body = request_json("POST", path, payload, auth=True)
    except CrptTokenError as e:
        raise CrptAuthError(str(e), None, e.user_message)
    if status_code in (401, 403):
        details = _auth_error_details(path, status_code, auth_present=True, body=body)
        raise CrptAuthError("Unauthorized", status_code, details)
    if status_code >= 500:
        raise CrptServiceError("Service error", status_code, body)
    is_error_status = status_code < 200 or status_code >= 300
    allow_error_payload = status_code == 404
    if is_error_status and not allow_error_payload:
        details = _request_error_details(path, status_code, body, payload)
        raise CrptRequestError("Bad response", status_code, details)
    try:
        data = json.loads(body) if body else {}
    except Exception as e:
        if is_error_status:
            details = _request_error_details(path, status_code, body, payload)
            raise CrptRequestError("Bad response", status_code, details)
        raise CrptRequestError(f"Invalid JSON: {e}", status_code, body)
    if is_error_status and allow_error_payload:
        if not _looks_like_codes_response(data):
            details = _request_error_details(path, status_code, body, payload)
            raise CrptRequestError("Bad response", status_code, details)

    items = _parse_short_list_response(data)
    bucket: dict[str, list[dict]] = {}
    for it in items:
        key = normalize_to_ki(it.get("code", ""))
        if not key:
            continue
        bucket.setdefault(key, []).append(it)

    results = []
    for item in chunk:
        key = item["normalized"]
        if key in bucket and bucket[key]:
            it = bucket[key].pop(0)
            status = (it.get("status") or "").strip()
            status_ex = (it.get("statusEx") or "").strip()
            label, introduced, severity = interpret_status(status, status_ex)
            results.append(CrptCodeResult(
                index=item["index"],
                code=item["code"],
                normalized=item["normalized"],
                status=status,
                status_ex=status_ex,
                explain=label,
                introduced=introduced,
                severity=severity,
                raw=it.get("raw"),
            ))
        else:
            label, introduced, severity = interpret_status("NOT_FOUND", None)
            results.append(CrptCodeResult(
                index=item["index"],
                code=item["code"],
                normalized=item["normalized"],
                status="NOT_FOUND",
                status_ex="",
                explain=label,
                introduced=introduced,
                severity=severity,
                raw=None,
                error="not_found",
            ))
    if results:
        nf_count = sum(1 for r in results if (r.status or "").upper() == "NOT_FOUND" or r.error == "not_found")
        if nf_count and (nf_count == len(results) or (nf_count / len(results)) >= 0.8):
            diag_samples = []
            for item in chunk[:2]:
                diag = item.get("diag") or {}
                diag_samples.append(
                    f"sent_ki_len={diag.get('sent_ki_len')}, has_gs_before_trim={diag.get('has_gs_before_trim')}"
                )
            diag_text = "; ".join(diag_samples) if diag_samples else "-"
            _log_diag(logger, f"[CRPT] NOT_FOUND \u043c\u0430\u0441\u0441\u043e\u0432\u043e: pg={cfg.pg}, {diag_text}")
    return results


def _chunk_items(items: list[dict], size: int) -> list[list[dict]]:
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


async def crpt_check_statuses(codes: list[str], use_info: bool = False, logger=None) -> list[CrptCodeResult]:
    cfg = get_crpt_config()
    if not cfg.pg:
        raise CrptClientError("\u041d\u0435 \u0437\u0430\u0434\u0430\u043d CRPT_PG (product group). \u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0432 .env, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: CRPT_PG=grocery")
    inputs = []
    for idx, code in enumerate(codes or []):
        normalized, diag = _normalize_to_ki_with_diag(code)
        inputs.append({"index": idx, "code": code, "normalized": normalized, "diag": diag})
    if not inputs:
        return []

    chunks = _chunk_items(inputs, cfg.max_codes_per_request)
    sem = asyncio.Semaphore(cfg.concurrency)
    loop = asyncio.get_running_loop()

    async def run_chunk(chunk: list[dict]):
        async with sem:
            return await loop.run_in_executor(None, _check_chunk_sync, chunk, use_info, logger)

    tasks = [asyncio.create_task(run_chunk(ch)) for ch in chunks]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    results_by_index: dict[int, CrptCodeResult] = {}

    for chunk, res in zip(chunks, gathered):
        if isinstance(res, Exception):
            details = None
            status = "ERROR"
            explain = "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0440\u043e\u0441\u0430"
            severity = "error"
            error_name = "error"
            if isinstance(res, CrptAuthError):
                if res.status_code in (401, 403):
                    explain = "\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430/\u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u043e\u0434\u043f\u0438\u0441\u0438/\u043d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 omsConnection/\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442"
                else:
                    explain = res.body if isinstance(res.body, str) and res.body else "\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430/\u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u043e\u0434\u043f\u0438\u0441\u0438/\u043d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 omsConnection/\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442"
                status = "AUTH_ERROR"
                error_name = "auth_error"
                details = res.body
            elif isinstance(res, CrptServiceError):
                explain = "\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u0435\u0440\u0432\u0438\u0441\u0430 \u0413\u0418\u0421 \u041c\u0422"
                status = "SERVICE_ERROR"
                error_name = "service_error"
                details = res.body
            elif isinstance(res, CrptRequestError):
                explain = "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0440\u043e\u0441\u0430"
                status = "REQUEST_ERROR"
                error_name = "request_error"
                details = res.body
            elif isinstance(res, CrptTransportError):
                explain = "\u0421\u0435\u0442\u0435\u0432\u0430\u044f \u043e\u0448\u0438\u0431\u043a\u0430"
                status = "NETWORK_ERROR"
                error_name = "network_error"
                details = str(res)
            for item in chunk:
                results_by_index[item["index"]] = _make_error_result(
                    item, status, explain, severity, error_name, details
                )
            continue
        for r in res:
            results_by_index[r.index] = r

    ordered = []
    for i in range(len(inputs)):
        if i in results_by_index:
            ordered.append(results_by_index[i])
    return ordered


def crpt_check_statuses_sync(codes: list[str], use_info: bool = False, logger=None) -> list[CrptCodeResult]:
    return asyncio.run(crpt_check_statuses(codes, use_info=use_info, logger=logger))


def check_codes_status(codes: list[str], use_info: bool = False, logger=None) -> list[CrptCodeResult]:
    return crpt_check_statuses_sync(codes, use_info=use_info, logger=logger)
